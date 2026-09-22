import time
import json
from pathlib import Path
import numpy as np
import onnxruntime as ort
import sentencepiece as spm
from typing import List
from .audio import NumPyMelExtractor, load_audio
from .encoder import SenseVoiceEncoder
from .decoder import SenseVoiceDecoder
from .schema import ASREngineConfig, TranscriptionResult, Timings, RecognitionResult

class SenseVoiceInference:
    """
    SenseVoice ONNX inference engine.
    - No PyTorch dependency.
    - Dynamic prompt construction.
    - ONNX CPU and DirectML support.
    """
    def __init__(self, config: ASREngineConfig):
        """
        Initialize SenseVoice inference.
        Accept one ASREngineConfig containing all settings.
        """
        self.config = config
        self.onnx_provider = config.onnx_provider
        # 1. Read explicit paths from configuration.
        encoder_path = config.encoder_path
        decoder_path = config.decoder_path
        tokenizer_path = config.tokenizer_path
 
        # 2. Construct encoder, decoder, and frontend.
        self.encoder = SenseVoiceEncoder(
            encoder_path=encoder_path, 
            onnx_provider=self.onnx_provider,
            dml_pad_to=self.config.dml_pad_to
        )
        self.decoder = SenseVoiceDecoder(
            decoder_path=decoder_path, 
            onnx_provider=self.onnx_provider,
            dml_pad_to=self.config.dml_pad_to
        )
        self.frontend = NumPyMelExtractor()
        
        # 3. Load the tokenizer from bytes to avoid Windows path encoding issues.
        self.sp = spm.SentencePieceProcessor()
        with open(tokenizer_path, 'rb') as f:
            self.sp.load_from_serialized_proto(f.read())
        
    def __call__(self, audio_data: np.ndarray, lid="auto", itn=True, chunk_size=40, overlap=5):
        """Choose single- or multi-segment recognition by audio duration."""
        return self.recognize(audio_data, lid=lid, itn=itn, chunk_size=chunk_size, overlap=overlap)

    def recognize(self, audio_data: np.ndarray, lid="auto", itn=True, chunk_size=40, overlap=5):
        """
        Recognize audio with automatic segmentation and merging.
        Treat short audio as a single segment of the same pipeline.
        """
        # 1. Extract features for the complete audio.
        lfr_feat = self.frontend.extract(audio_data)
        
        # 2. Split by LFR frames.
        # Use 100/6 frames per second rather than the rounded 16.6.
        chunk_frames = int(chunk_size * 100 / 6)
        overlap_frames = int(overlap * 100 / 6)
        stride = max(1, chunk_frames - overlap_frames)
        
        all_results = []
        for start in range(0, len(lfr_feat), stride):
            end = min(start + chunk_frames, len(lfr_feat))
            chunk_lfr = lfr_feat[start:end]
            
            # Recognize one segment.
            offset_sec = (start * 6 * 0.01) # Each frame spans 0.06 seconds.
            res = self._recognize_lfr(chunk_lfr, lid=lid, itn=itn, offset_sec=offset_sec)
            all_results.append(res)
            
            # Stop at the end of the audio.
            if end == len(lfr_feat):
                break
                
        # 3. Merge results through SequenceMatcher.
        # A single result is returned unchanged, preserving its complete timing statistics.
        return self._merge_results(all_results, overlap)

    def transcribe(self, audio_file: str, lid="auto", itn=True, chunk_size=40, overlap=5, start_second=None, duration=None):
        """Load audio and run the transcription pipeline."""
        audio = load_audio(audio_file, start_second=start_second, duration=duration)
        return self.recognize(audio, lid=lid, itn=itn, chunk_size=chunk_size, overlap=overlap)


    def _recognize_lfr(self, lfr_feat: np.ndarray, lid="auto", itn=True, offset_sec=0.0):
        """
        Low-level recognition.
        Accept LFR features and return results with global time offsets.
        """
        t_start = time.perf_counter()
        
        # 1. Run the encoder.
        t0 = time.perf_counter()
        enc_out = self.encoder.forward(lfr_feat, lid=lid, itn=itn)
        t_encoder = time.perf_counter() - t0
        
        # 2. Run the decoder.
        t0 = time.perf_counter()
        T_valid = lfr_feat.shape[0]
        greedy_results = self.decoder.decode(
            enc_out, self.sp, T_valid=T_valid
        )
        t_decoder = time.perf_counter() - t0
        
        recognition_results = []
        for item in greedy_results:
            recognition_results.append(RecognitionResult(
                text=item["text"], 
                start=round(item["start"] + offset_sec, 3), 
            ))
            
        t_total = time.perf_counter() - t_start
        
        return TranscriptionResult(
            text="".join([r.text for r in recognition_results]),
            results=recognition_results,
            timings=Timings(frontend=0, encoder=t_encoder, decoder=t_decoder, total=t_total)
        )

    def _merge_results(self, results_list: List[TranscriptionResult], overlap_sec: float):
        """
        Merge results through SequenceMatcher.
        """
        if not results_list: return None
        if len(results_list) == 1: return results_list[0]
        
        import difflib
        
        merged_results = list(results_list[0].results)
        
        for i in range(1, len(results_list)):
            new_res = results_list[i].results
            if not new_res: continue
            if not merged_results:
                merged_results.extend(new_res)
                continue
            
            # 1. Extract overlapping text.
            # Select twice the overlap duration from the previous tail.
            # Select twice the overlap duration from the new head.
            overlap_window = overlap_sec * 2.0
            
            last_time = merged_results[-1].start
            prev_overlap_indices = [idx for idx, r in enumerate(merged_results) if r.start >= last_time - overlap_window]
            new_overlap_indices = [idx for idx, r in enumerate(new_res) if r.start <= new_res[0].start + overlap_window]
            
            prev_overlap_text = "".join([merged_results[idx].text for idx in prev_overlap_indices])
            new_overlap_text = "".join([new_res[idx].text for idx in new_overlap_indices])
            
            # 2. Find the longest matching block.
            sm = difflib.SequenceMatcher(None, prev_overlap_text, new_overlap_text)
            match = sm.find_longest_match(0, len(prev_overlap_text), 0, len(new_overlap_text))
            
            if match.size >= 1:
                # Find the previous result's truncation boundary.
                char_count = 0
                prev_cut_idx = prev_overlap_indices[-1] + 1
                for idx in prev_overlap_indices:
                    char_count += len(merged_results[idx].text)
                    if char_count > match.a + match.size // 2: # Split at the match midpoint.
                        prev_cut_idx = idx
                        break
                
                # Find the new result's start boundary.
                char_count = 0
                new_start_idx = 0
                for idx in new_overlap_indices:
                    char_count += len(new_res[idx].text)
                    if char_count > match.b + match.size // 2:
                        new_start_idx = idx + 1
                        break
                
                # Merge the results.
                merged_results = merged_results[:prev_cut_idx] + new_res[new_start_idx:]
            else:
                # Fall back to timestamp-based concatenation.
                last_t = merged_results[-1].start
                new_start_idx = 0
                for idx, r in enumerate(new_res):
                    if r.start > last_t:
                        new_start_idx = idx
                        break
                else:
                    new_start_idx = len(new_res)
                merged_results.extend(new_res[new_start_idx:])
        
        # Split spaces into independent tokens after merging.
        expanded_results = []
        for r in merged_results:
            parts = r.text.split(" ")
            for i, part in enumerate(parts):
                if i > 0:
                    expanded_results.append(RecognitionResult(
                        text=" ",
                        start=r.start,
                    ))
                if part:
                    expanded_results.append(RecognitionResult(
                        text=part,
                        start=r.start,
                    ))
        merged_results = expanded_results

        return TranscriptionResult(
            text="".join([r.text for r in merged_results]),
            results=merged_results,
            timings=Timings() # Aggregate timings are not retained after a multi-segment merge.
        )
