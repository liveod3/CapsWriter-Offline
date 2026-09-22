
from core.i18n import tr
import os
import time
from typing import Optional, List, Dict, Any

from .schema import TranscriptionResult, RecognitionStream
from .audio import load_audio
from .text_merge import merge_transcription_results
from .display import DisplayReporter
from .srt_utils import generate_srt_file
from .utils import timer


class AudioTranscriber:
    """Segment, transcribe, and merge short or long audio files."""
    def __init__(self, pipeline, sample_rate: int = 16000):
        self.pipeline = pipeline
        self.sample_rate = sample_rate

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        context: Optional[str] = None,
        verbose: bool = True,
        segment_size: float = 60.0,
        overlap: float = 2.0,
        start_second: Optional[float] = None,
        duration: Optional[float] = None,
        srt: bool = False,
        temperature: float = 0.4,
        top_p: float = 1.0,
        top_k: int = 50
    ) -> TranscriptionResult:
        result = TranscriptionResult()
        
        reporter = DisplayReporter(verbose=verbose)
        try:
            self._print_header(reporter, audio_path)

            reporter.print(tr('terminal.transcriber.loading_audio'))
            audio, result.timings.load_audio = timer(
                load_audio,
                audio_path, 
                self.sample_rate, 
                start_second=start_second, 
                duration=duration
            )
            
            audio_duration = len(audio) / self.sample_rate
            reporter.print(tr('terminal.transcriber.audio_duration_s', value0=audio_duration))
            if start_second: reporter.print(tr('terminal.transcriber.start_offset_s', value0=start_second))

            base_offset = start_second if start_second else 0.0

            _, result.timings.total = timer(
                self._process_audio, audio, result, language, context, verbose, segment_size, overlap, reporter,
                base_offset, temperature=temperature, top_p=top_p, top_k=top_k
            )

            self._print_stats(reporter, result)

            # 3. Export SRT
            if srt and result.segments:
                srt_path = os.path.splitext(audio_path)[0] + ".srt"
                generate_srt_file(result.segments, srt_path)
                reporter.print(tr('terminal.transcriber.subtitles_exported_to', value0=os.path.basename(srt_path)), force=True)

            if result.text:
                reporter.print("\n" + "-"*30 + ' ' + tr('engine.full_text') + ' ' + "-"*30, force=True)
                reporter.print(result.text, force=True)
                reporter.print("-" * 74 + "\n", force=True)

            return result

        except Exception as e:
            reporter.print(tr('terminal.transcriber.transcription_failed', value0=e), force=True)
            raise
        
        finally:
            reporter.stop()

    def _process_audio(self, audio, result, language, context, verbose, segment_size, overlap, reporter, base_offset,
                       temperature=0.8, top_p=1.0, top_k=50):
        audio_duration = len(audio) / self.sample_rate
        
        segments_info = list(self._generate_segments(audio_duration, segment_size, overlap))
        is_multi = len(segments_info) > 1

        if is_multi:
            reporter.print(tr('terminal.transcriber.long_audio_detected_enabling_segmented_recognition'), force=True)
            reporter.skip_technical = True

        segment_results = []

        for idx, (s_s, e_s) in enumerate(segments_info):
            if is_multi:
                reporter.set_segment(idx + 1, len(segments_info))
                reporter.print(tr('terminal.transcriber.processing_segment_s_s', value0=s_s, value1=e_s), force=True)
            
            chunk = audio[int(s_s * self.sample_rate):int(e_s * self.sample_rate)]
            stream = RecognitionStream()
            stream.accept_waveform(self.sample_rate, chunk)
            
            # Preserve verbose for one segment; let individual segments handle detailed multi-segment output.
            d_res = self.pipeline.decode_stream(stream, language, context, verbose if not is_multi else True, reporter,
                                               temperature=temperature, top_p=top_p, top_k=top_k)
            
            segment_results.append({
                'text': d_res.text,
                'segments': d_res.aligned,
                'duration': e_s - s_s,
                'ctc_text': "".join([r.text for r in d_res.ctc_results]) if d_res.ctc_results else ""
            })
            
            # Accumulate timings
            result.timings += d_res.timings

        # Finalize and merge results.
        if len(segments_info) > 1:
            reporter.set_segment(0, 0)
            reporter.skip_technical = False
        
        # Share merge logic between single- and multi-segment inputs.
        offsets = [s[0] + base_offset for s in segments_info]
        full_text, full_segs = merge_transcription_results(segment_results, offsets, overlap)
        result.text = full_text
        result.segments = full_segs
        
        all_h = set()
        all_ctc = []
        for r in segment_results:
            if r['ctc_text']: all_ctc.append(r['ctc_text'])
        result.ctc_text = "".join(all_ctc)

    def _generate_segments(self, duration: float, segment_size: float, overlap: float):
        """Yield audio segment start/end times."""
        if duration <= segment_size + 2.0:
            yield (0.0, duration)
            return

        step = segment_size - overlap
        curr = 0.0
        while curr < duration:
            end = min(curr + segment_size, duration)
            yield (curr, end)
            if end >= duration: break
            curr += step

    def _print_header(self, reporter, audio_path):
        line = "=" * 70
        reporter.print(f"\n{line}", force=True)
        reporter.print(tr('terminal.transcriber.processing_audio', value0=os.path.basename(audio_path)), force=True)
        reporter.print(f"{line}", force=True)

    def _print_stats(self, reporter, result):
        reporter.print(tr('terminal.transcriber.transcription_timing'))
        reporter.print(tr('terminal.transcriber.audio_encoding_ms', value0=result.timings.encode * 1000))
        reporter.print(tr('terminal.transcriber.ctc_decoding_ms', value0=result.timings.ctc * 1000))
        reporter.print(tr('terminal.transcriber.llm_prefill_ms', value0=result.timings.inject * 1000))
        reporter.print(tr('terminal.transcriber.llm_generation_ms', value0=result.timings.llm_generate * 1000))
        reporter.print(tr('terminal.transcriber.total_elapsed_s', value0=result.timings.total))
