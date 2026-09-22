# coding=utf-8

from core.i18n import tr
import os
import time
from pathlib import Path
import numpy as np
import onnxruntime as ort


class FastWhisperMel:
    """Extract Mel features with NumPy without librosa/numba JIT startup."""
    def __init__(self, filter_path: str = None, n_mels=128, sr=16000, n_fft=400, f_min=0, f_max=8000, norm="slaney", mel_scale="slaney"):
        self.n_fft = n_fft
        self.hop_length = 160
        self.n_mels = n_mels
        
        if filter_path and os.path.exists(filter_path):
            self.filters = np.load(filter_path)
        else:
            self.filters = self._generate_filters(sr, n_fft, n_mels, f_min, f_max, norm, mel_scale)
            
        # Cache the Hann window used by the Qwen3/Whisper frontend.
        self.window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(self.n_fft) / self.n_fft)
        
    def _generate_filters(self, sr, n_fft, n_mels, f_min, f_max, norm, mel_scale):
        """
        Build a Mel filter bank with torchaudio-compatible conventions.
        norm: 'slaney' area normalization, or None.
        mel_scale: 'slaney' piecewise linear/logarithmic, or 'htk' logarithmic.
        """
        def hz_to_mel(freq, scale):
            if scale == "htk":
                return 2595.0 * np.log10(1.0 + (freq / 700.0))
            # Slaney Scale (Linear + Log)
            f_min_sl, f_sp_sl = 0.0, 200.0 / 3
            mels = (freq - f_min_sl) / f_sp_sl
            min_log_hz, logstep = 1000.0, np.log(6.4) / 27.0
            min_log_mel = (min_log_hz - f_min_sl) / f_sp_sl
            if isinstance(freq, np.ndarray):
                mask = freq >= min_log_hz
                mels[mask] = min_log_mel + np.log(freq[mask] / min_log_hz) / logstep
            elif freq >= min_log_hz:
                mels = min_log_mel + np.log(freq / min_log_hz) / logstep
            return mels

        def mel_to_hz(mels, scale):
            if scale == "htk":
                return 700.0 * (10.0 ** (mels / 2595.0) - 1.0)
            # Slaney Scale (Linear + Log)
            f_min_sl, f_sp_sl = 0.0, 200.0 / 3
            freqs = f_min_sl + f_sp_sl * mels
            min_log_hz, logstep = 1000.0, np.log(6.4) / 27.0
            min_log_mel = (min_log_hz - f_min_sl) / f_sp_sl
            if isinstance(mels, np.ndarray):
                mask = mels >= min_log_mel
                freqs[mask] = min_log_hz * np.exp(logstep * (mels[mask] - min_log_mel))
            elif mels >= min_log_mel:
                freqs = min_log_hz * np.exp(logstep * (mels - min_log_mel))
            return freqs

        n_freqs = n_fft // 2 + 1
        all_freqs = np.linspace(0, sr // 2, n_freqs)
        m_pts = np.linspace(hz_to_mel(f_min, mel_scale), hz_to_mel(f_max, mel_scale), n_mels + 2)
        f_pts = mel_to_hz(m_pts, mel_scale)
        f_diff = f_pts[1:] - f_pts[:-1]
        slopes = f_pts[np.newaxis, :] - all_freqs[:, np.newaxis]
        down_slopes = (-1.0 * slopes[:, :-2]) / f_diff[:-1]
        up_slopes = slopes[:, 2:] / f_diff[1:]
        fb = np.maximum(0, np.minimum(down_slopes, up_slopes))
        
        # Area Normalization
        if norm == "slaney":
            enorm = 2.0 / (f_pts[2 : n_mels + 2] - f_pts[:n_mels])
            fb *= enorm[np.newaxis, :]
            
        return fb.astype(np.float32)
        
    def __call__(self, audio: np.ndarray, dtype=np.float32) -> np.ndarray:
        # 1. Pad using librosa's center=True convention.
        pad_len = int(self.n_fft // 2)
        y = np.pad(audio, pad_len, mode='reflect')
        
        # 2. Create frame views without copying.
        num_frames = 1 + (len(y) - self.n_fft) // self.hop_length
        shape = (self.n_fft, num_frames)
        strides = (y.itemsize, self.hop_length * y.itemsize)
        frames = np.lib.stride_tricks.as_strided(y, shape=shape, strides=strides)
        
        # 3. Apply the window and real FFT.
        stft_res = np.fft.rfft(frames * self.window[:, np.newaxis], axis=0)
        
        # 4. Compute the power spectrum.
        magnitudes = np.abs(stft_res) ** 2
        
        # 5. Apply Mel filters.
        mel_spec = np.dot(self.filters.T, magnitudes)
        
        # 6. Take logarithms.
        log_spec = np.log10(np.maximum(mel_spec, 1e-10))
        
        # 7. Normalize.
        log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
        log_spec = (log_spec + 4.0) / 4.0
        
        # 8. Drop excess frames.
        n_frames_out = audio.shape[-1] // self.hop_length
        log_spec = log_spec[:, :n_frames_out]
        
        return log_spec.astype(dtype)

def get_feat_extract_output_lengths(input_lengths):
    """
    Compute the valid output frame count using Qwen3 frontend conventions.
    Slice valid frames from the concatenated N*13 output.
    """
    input_lengths_leave = input_lengths % 100
    feat_lengths = (input_lengths_leave - 1) // 2 + 1
    output_lengths = ((feat_lengths - 1) // 2 + 1 - 1) // 2 + 1 + (input_lengths // 100) * 13
    return int(output_lengths)

class QwenAudioEncoder:
    """Qwen3 audio encoder with split frontend and backend."""
    def __init__(self, frontend_path: str, backend_path: str, onnx_provider: str = 'CPU', dml_pad_to: int = 30, verbose: bool = True):
        self.verbose = verbose
        self.onnx_provider = onnx_provider.upper()
        self.active_dml = False
        self.dml_pad_to = dml_pad_to
        # Precompute padded length at 13 hidden-state frames per audio second.
        self.h_target_len = self.dml_pad_to * 13
        
        # Initialize ONNX session options.
        sess_opts = ort.SessionOptions()
        # DirectML requires disabled memory patterns and sequential execution.
        # Set both explicitly for predictable concurrent model resource ownership.
        sess_opts.enable_mem_pattern = False
        sess_opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_opts.log_severity_level = 3
        sess_opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        sess_opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        
        available_providers = ort.get_available_providers()
        providers = ['CPUExecutionProvider']
        
        if self.onnx_provider in ('TRT', 'TENSORRT') and 'TensorrtExecutionProvider' in available_providers:
            providers.insert(0, ('TensorrtExecutionProvider', {
                'trt_fp16_enable': True,
                'trt_engine_cache_enable': True,
                'trt_engine_cache_path': Path(backend_path).parent / 'trt_cache',
            }))
        elif self.onnx_provider == 'DML' and 'DmlExecutionProvider' in available_providers:
            providers.insert(0, 'DmlExecutionProvider') 
            self.active_dml = True
        elif self.onnx_provider == 'CUDA' and 'CUDAExecutionProvider' in available_providers:
            providers.insert(0, 'CUDAExecutionProvider')
            
        if self.verbose: 
            print(tr('terminal.encoder.encoder_loading_split_onnx_models_provider_pad_s', value0=providers[0], value1=dml_pad_to))
            print(tr('terminal.encoder.frontend', value0=os.path.basename(frontend_path)))
            print(tr('terminal.encoder.backend', value0=os.path.basename(backend_path)))

        # Load both sessions.
        self.sess_fe = ort.InferenceSession(frontend_path, sess_options=sess_opts, providers=providers)
        self.sess_be = ort.InferenceSession(backend_path, sess_options=sess_opts, providers=providers)
        
        self.mel_extractor = FastWhisperMel()
        
        # Detect precision from the frontend.
        try:
            fe_input_type = self.sess_fe.get_inputs()[0].type
            self.input_dtype = np.float16 if 'float16' in fe_input_type else np.float32
        except:
            self.input_dtype = np.float32

        # Warm up the encoder.
        if self.dml_pad_to > 0 and self.active_dml:
            if self.verbose: print(tr('terminal.encoder.encoder_warming_up_fixed_shape_s', value0=self.dml_pad_to))
            dummy_wav = np.zeros(int(16000 * self.dml_pad_to)).astype(np.float32)
            _ = self.encode(dummy_wav)
        else:
            # Non-DML execution only needs short unpadded warmup audio.
            if self.verbose: print(tr('terminal.encoder.encoder_warming_up_non_dml_mode'))
            dummy_wav = np.zeros(int(16000 * 2.0)).astype(np.float32)
            _ = self.encode(dummy_wav)
        if self.verbose: print(tr('terminal.encoder.encoder_warmup_complete'))

    def _run_frontend(self, mel: np.ndarray) -> np.ndarray:
        """Run frontend padding, chunk inference, concatenation, and valid-frame slicing."""
        T = mel.shape[1]
        
        # 1. Pad to a multiple of 100.
        pad_len = (100 - (T % 100)) % 100
        if pad_len > 0:
            mel = np.pad(mel, ((0,0), (0, pad_len)), mode='constant')
        
        # Add a batch dimension: (1, 128, T_padded).
        mel_input = mel[np.newaxis, ...]
        
        num_chunks = mel_input.shape[2] // 100
        fe_outputs = []
        chunk_size = 100
        
        # 2. Run each chunk.
        for i in range(num_chunks):
            start = i * chunk_size
            chunk = mel_input[:, :, start : start + chunk_size]
            out = self.sess_fe.run(None, {"chunk_mel": chunk})[0] # (1, 13, 896/1024)
            fe_outputs.append(out)
            
        # 3. Concatenate to (1, N_frames, D).
        hidden_states = np.concatenate(fe_outputs, axis=1)
        
        # 4. Remove trailing frames introduced by padding.
        t_out = get_feat_extract_output_lengths(T)
        hidden_states = hidden_states[:, :t_out, :]
        
        return hidden_states

    def _run_backend(self, hidden_states: np.ndarray) -> np.ndarray:
        """Run masked backend inference with optional fixed-shape padding."""
        batch, seq_len, dim = hidden_states.shape
        
        # 1. Validate shape and pad for DML only.
        if self.active_dml and seq_len < self.h_target_len:
            pad_width = self.h_target_len - seq_len
            # Zero-pad hidden states to (Batch, T_fixed, D).
            hidden_input = np.pad(hidden_states, ((0,0), (0, pad_width), (0,0)), mode='constant')
            
            # Mask valid positions with 0 and padded positions with -10000.0.
            # Broadcast to (Batch, 1, T_fixed, T_fixed).
            mask = np.zeros((batch, 1, self.h_target_len, self.h_target_len), dtype=self.input_dtype)
            mask[:, :, :, seq_len:] = -10000.0
        else:
            hidden_input = hidden_states
            mask = np.zeros((batch, 1, seq_len, seq_len), dtype=self.input_dtype)
        
        # 2. Run inference.
        audio_embd = self.sess_be.run(None, {
            "hidden_states": hidden_input,
            "attention_mask": mask
        })[0]
        
        # 3. Slice output to (Batch, seq_len, D).
        if audio_embd.shape[1] > seq_len:
            audio_embd = audio_embd[:, :seq_len, :]
            
        return audio_embd

    def encode(self, audio: np.ndarray) -> tuple:
        """Encode Mel features through frontend/backend; return embeddings and elapsed time."""
        t0 = time.time()
        
        # 1. Extract Mel features.
        # audio: (N_samples,) -> mel: (128, T)
        mel = self.mel_extractor(audio, dtype=self.input_dtype) 
        
        # 2. Frontend (Loop)
        hidden_states = self._run_frontend(mel)
        
        # 3. Backend (Transformer)
        audio_embd = self._run_backend(hidden_states)
        
        # 4. Remove the batch dimension to obtain (T, D).
        if audio_embd.ndim == 3: 
            audio_embd = audio_embd[0]
            
        elapsed = time.time() - t0
        return audio_embd, elapsed
