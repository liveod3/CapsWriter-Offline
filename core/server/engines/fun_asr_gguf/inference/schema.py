"""
FunASR GGUF data types.

Define inference dataclasses with explicit fields and type annotations.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import numpy as np


# Recognition results.

@dataclass
class RecognitionResult:
    """
    Recognition result compatible with sherpa-onnx.

    Attributes:
        text: Recognized text.
        timestamps: Character timestamps in seconds.
        tokens: Character or token list.
    """
    text: str = ""
    timestamps: List[float] = field(default_factory=list)
    tokens: List[str] = field(default_factory=list)


@dataclass
class RecognitionStream:
    """
    Recognition stream compatible with sherpa-onnx.

    Carry audio samples and recognition results.

    Attributes:
        sample_rate: Audio sample rate.
        audio_data: float32 NumPy audio array.
        _result: Internal recognition result.
    """
    sample_rate: int = 16000
    audio_data: Optional[np.ndarray] = None
    _result: Optional[RecognitionResult] = field(default=None, init=False, repr=False)

    def accept_waveform(self, sample_rate: int, audio: np.ndarray):
        """
        Accept audio through the sherpa-onnx-compatible interface.

        Args:
            sample_rate: Sample rate.
            audio: float32 NumPy audio array.
        """
        self.sample_rate = sample_rate
        self.audio_data = audio.astype(np.float32)

    @property
    def result(self) -> RecognitionResult:
        """Return the sherpa-onnx-compatible recognition result."""
        if self._result is None:
            self._result = RecognitionResult()
        return self._result

    def set_result(self, text: str, timestamps: List[float] = None, tokens: List[str] = None):
        """Set the internal recognition result."""
        self._result = RecognitionResult(
            text=text,
            timestamps=timestamps or [],
            tokens=tokens or []
        )


@dataclass
class Timings:
    """
    Stage timings in seconds.

    Attributes:
        encode: Audio encoding time.
        ctc: CTC decoding time.
        prepare: Prompt preparation time.
        inject: Embedding injection time.
        llm_generate: Text generation time.
        align: Alignment time.
        total: Total elapsed time.
    """
    encode: float = 0.0
    ctc: float = 0.0
    prepare: float = 0.0
    inject: float = 0.0
    llm_generate: float = 0.0
    align: float = 0.0
    total: float = 0.0

    def __iadd__(self, other: 'Timings') -> 'Timings':
        self.encode += getattr(other, 'encode', 0.0)
        self.ctc += getattr(other, 'ctc', 0.0)
        self.prepare += getattr(other, 'prepare', 0.0)
        self.inject += getattr(other, 'inject', 0.0)
        self.llm_generate += getattr(other, 'llm_generate', 0.0)
        self.align += getattr(other, 'align', 0.0)
        return self


@dataclass
class TranscriptionResult:
    """
    Complete transcription result.

    Attributes:
        text: Recognized text.
        segments: Timed segments.
        ctc_text: CTC recognition text.
        timings: Stage timings.
    """
    text: str = ""
    segments: List[Dict[str, Any]] = field(default_factory=list)
    ctc_text: str = ""
    timings: Timings = field(default_factory=Timings)


# Engine settings.

@dataclass
class ASREngineConfig:
    """
    ASR engine settings.

    Attributes:
        encoder_onnx_path: Encoder ONNX path.
        ctc_onnx_path: CTC ONNX path.
        decoder_gguf_path: Decoder GGUF path.
        tokens_path: Vocabulary path.
        enable_ctc: Enable CTC.
        n_predict: Maximum generated tokens.
        n_threads: Thread count; None selects automatically.
        n_threads_batch: Batch thread count; None selects automatically.
        n_ubatch: llama.cpp physical batch size.
        sample_rate: Audio sample rate.
        onnx_provider: CPU, CUDA, DML, or TensorRT execution provider.
        dml_pad_to: DirectML padding duration in seconds.
        verbose: Display detailed loading messages.
    """
    encoder_onnx_path: str
    ctc_onnx_path: str
    decoder_gguf_path: str
    tokens_path: str
    enable_ctc: bool = True
    n_predict: int = 512
    n_threads: Optional[int] = None
    n_threads_batch: Optional[int] = None
    n_ubatch: int = 512
    sample_rate: int = 16000
    onnx_provider: str = 'CPU'  # CPU, CUDA, DML, TensorRT
    dml_pad_to: int = 30
    llm_use_gpu: bool = True
    vulkan_force_fp32: bool = False
    verbose: bool = True


# CTC results.

@dataclass
class CTCResult:
    """
    CTC decoding result.

    Attributes:
        text: Recognized character or word.
        timestamp: Timestamp in seconds.
        score: Confidence score.
    """
    text: str
    timestamp: float
    score: float = 1.0


# Inference statistics.

@dataclass
class Statistics:
    """
    Inference statistics.

    Attributes:
        audio_duration: Audio duration in seconds.
        n_input_tokens: Input token count.
        n_prefix_tokens: Prefix token count.
        n_audio_tokens: Audio embedding token count.
        n_suffix_tokens: Suffix token count.
        n_generated_tokens: Generated token count.
        tps_in: Input tokens per second.
        tps_out: Output tokens per second.
    """
    audio_duration: float = 0.0
    n_input_tokens: int = 0
    n_prefix_tokens: int = 0
    n_audio_tokens: int = 0
    n_suffix_tokens: int = 0
    n_generated_tokens: int = 0
    tps_in: float = 0.0
    tps_out: float = 0.0

    def __str__(self) -> str:
        """Format inference statistics."""
        from core.i18n import tr
        return tr(
            'engine.statistics', duration=self.audio_duration,
            input_speed=self.tps_in, inputs=self.n_input_tokens,
            prefix=self.n_prefix_tokens, audio=self.n_audio_tokens,
            suffix=self.n_suffix_tokens, output_speed=self.tps_out,
            outputs=self.n_generated_tokens,
        )


@dataclass
class DecodeResult:
    """
    Internal complete result returned by decode_stream.

    Attributes:
        text: Recognized text.
        ctc_results: CTC result list.
        aligned: Timestamp alignment results.
        audio_embd: Audio embeddings.
        n_prefix: Prefix token count.
        n_suffix: Suffix token count.
        n_gen: Generated token count.
        timings: Stage timings.
    """
    text: str = ""
    ctc_results: List = field(default_factory=list)
    aligned: List[List[Any]] = field(default_factory=list)
    audio_embd: Optional[np.ndarray] = None
    n_prefix: int = 0
    n_suffix: int = 0
    n_gen: int = 0
    timings: Timings = field(default_factory=Timings)
    is_aborted: bool = False

@dataclass
class LLMDecodeResult:
    """
    Decoder generation result.

    Attributes:
        text: Generated text.
        n_gen: Generated token count.
        t_inject: Embedding injection time.
        t_gen: Generation time.
        is_aborted: Whether a circuit breaker interrupted generation.
    """
    text: str = ""
    n_gen: int = 0
    t_inject: float = 0.0
    t_gen: float = 0.0
    is_aborted: bool = False


# Public exports.

__all__ = [
    # Recognition results.
    'RecognitionResult',
    'RecognitionStream',
    'TranscriptionResult',
    'DecodeResult',
    'LLMDecodeResult',

    # Settings.
    'ASREngineConfig',

    # Timings.
    'Timings',

    # CTC
    'CTCResult',

    # Statistics.
    'Statistics',
]
