"""
Audio preprocessing utilities.
Load supported audio formats directly through FFmpeg.
"""

from core.i18n import Notice
import os
import math
import shutil
import subprocess
import numpy as np
import soundfile as sf
from pathlib import Path
from . import logger


def numpy_resample_poly(x, up, down, window_size=10):
    """
    NumPy polyphase resampling.
    Follow scipy.signal.resample_poly's filter and phase approach.
    """
    # 1. Reduce the rate ratio.
    g = math.gcd(up, down)
    up //= g
    down //= g

    if up == down:
        return x.copy()

    # 2. Design the FIR filter using the firwin convention.
    max_rate = max(up, down)
    f_c = 1.0 / max_rate  
    half_len = window_size * max_rate
    n_taps = 2 * half_len + 1
    
    t = np.arange(n_taps) - half_len
    h = np.sinc(f_c * t)
    
    # Use a Kaiser window with beta=5.0.
    # np.i0 computes the modified Bessel function of the first kind, order zero.
    beta = 5.0
    kaiser_win = np.i0(beta * np.sqrt(1 - (2 * t / (n_taps - 1))**2)) / np.i0(beta)
    h = h * kaiser_win
    h = h * (up / np.sum(h))

    # 3. Apply polyphase filtering with upfirdn-style indexing.
    length_in = len(x)
    length_out = int(math.ceil(length_in * up / down))
    
    x_up = np.zeros(length_in * up + n_taps, dtype=np.float32)
    x_up[:length_in * up:up] = x
    
    y_full = np.convolve(x_up, h, mode='full')
    
    offset = (n_taps - 1) // 2
    y = y_full[offset : offset + length_in * up : down]
    
    return y[:length_out].astype(np.float32)


def resample_audio(audio, sr, target_sr):
    """Resample audio."""
    if sr == target_sr:
        return audio
    return numpy_resample_poly(audio, target_sr, sr)


def load_audio_numpy(audio_path, sample_rate=24000, start_second=None, duration=None):
    """Load through soundfile and resample with NumPy."""
    info = sf.info(audio_path)
    sr = info.samplerate
    
    # Resolve the start offset.
    start_frame = int(start_second * sr) if start_second is not None else 0
    frames = int(duration * sr) if duration is not None else -1
    
    audio, sr = sf.read(audio_path, start=start_frame, frames=frames, dtype='float32')
    
    # Convert to mono.
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
        
    # Resample audio.
    if sr != sample_rate:
        audio = resample_audio(audio, sr, sample_rate)
        
    return audio.astype(np.float32)


def check_ffmpeg():
    """Check FFmpeg availability."""
    return shutil.which('ffmpeg') is not None


def load_audio_ffmpeg(audio_path, sample_rate=24000, start_second=None, duration=None):
    """Read audio directly through FFmpeg."""
    if not check_ffmpeg():
        raise RuntimeError(Notice('validation.audio.ffmpeg_not_found_install_ffmpeg_and_add_it'))

    cmd = ['ffmpeg', '-y', '-i', str(audio_path)]

    if start_second is not None:
        cmd.extend(['-ss', str(start_second)])
    if duration is not None:
        cmd.extend(['-t', str(duration)])

    cmd.extend([
        '-ar', str(sample_rate),
        '-ac', '1',
        '-f', 'f32le',
        'pipe:1'
    ])

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0
    )

    raw_bytes, stderr = process.communicate()

    if process.returncode != 0:
        error_msg = stderr.decode('utf-8', errors='ignore')
        raise RuntimeError(Notice('validation.audio.ffmpeg_audio_processing_failed', value0=error_msg))

    return np.frombuffer(raw_bytes, dtype=np.float32)



def load_audio(audio_path, sample_rate=16000, start_second=None, duration=None):
    """
    Load an audio file.
    Select the reader by extension:
    - soundfile: .wav, .flac, .ogg, and .mp3.
    - FFmpeg fallback: .m4a, .mp4, .opus, .wmv, and other formats.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(Notice('validation.audio.audio_file_does_not_exist', value0=audio_path))
        
    # Read the extension.
    ext = Path(audio_path).suffix.lower()
    
    # Formats handled by the soundfile path.
    SF_FORMATS = {'.wav', '.flac', '.ogg', '.mp3'}
    
    if ext in SF_FORMATS:
        return load_audio_numpy(audio_path, sample_rate, start_second, duration)
    else:
        return load_audio_ffmpeg(audio_path, sample_rate, start_second, duration)
