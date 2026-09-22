"""
SenseVoice ONNX data types.

Define inference dataclasses with explicit fields and type annotations.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import numpy as np
from pathlib import Path


# Recognition results.

@dataclass
class RecognitionResult:
    """
    One recognition unit, either a character or token block.

    Attributes:
        text: Recognized character or block.
        start: Start time in seconds.
    """
    text: str
    start: float


@dataclass
class RecognitionStream:
    """
    Recognition stream.

    Carry audio and results through a compatible stream interface.

    Attributes:
        sample_rate: Audio sample rate.
        audio_data: float32 NumPy audio array.
        results: Recognition results.
    """
    sample_rate: int = 16000
    audio_data: Optional[np.ndarray] = None
    results: List[RecognitionResult] = field(default_factory=list)

    def accept_waveform(self, sample_rate: int, audio: np.ndarray):
        """Accept audio samples."""
        self.sample_rate = sample_rate
        # Normalize samples to float32.
        if audio.dtype != np.float32:
            if audio.dtype == np.int16:
                audio = audio.astype(np.float32) / 32768.0
            else:
                audio = audio.astype(np.float32)
        self.audio_data = audio

    @property
    def text(self) -> str:
        """Return the complete merged text."""
        return "".join([r.text for r in self.results])


@dataclass
class Timings:
    """
    Stage timings in seconds.

    Attributes:
        frontend: Feature extraction time.
        encoder: Encoder inference time.
        decoder: CTC inference time.
        total: Total elapsed time.
    """
    frontend: float = 0.0
    encoder: float = 0.0
    decoder: float = 0.0
    total: float = 0.0


@dataclass
class TranscriptionResult:
    """
    Complete transcription result wrapper.

    Attributes:
        text: Final recognized text.
        results: Detailed RecognitionResult entries.
        timings: Timing statistics.
    """
    text: str = ""
    results: List[RecognitionResult] = field(default_factory=list)
    timings: Timings = field(default_factory=Timings)


# Engine settings.

@dataclass
class ASREngineConfig:
    """
    ASR engine settings.

    Attributes:
        encoder_path: Encoder ONNX path.
        decoder_path: Decoder ONNX path.
        tokenizer_path: Tokenizer model path.
        onnx_provider: CPU, CUDA, DML, or TensorRT execution provider.
        itn: Enable inverse text normalization.
        dml_pad_to: DirectML padding duration in seconds.
    """
    encoder_path: str
    decoder_path: str
    tokenizer_path: str
    onnx_provider: str = "cpu"
    itn: bool = True
    dml_pad_to: int = 30


# Public exports.

__all__ = [
    'RecognitionResult',
    'RecognitionStream',
    'TranscriptionResult',
    'ASREngineConfig',
    'Timings',
]
