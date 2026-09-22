# coding=utf-8
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, List, Optional
import numpy as np

class MsgType(Enum):
    CMD_ENCODE = auto()   # Parent -> encoder: encoding request.
    CMD_ALIGN = auto()    # Parent -> aligner: alignment request.
    CMD_STOP = auto()     # Parent -> worker: stop request.
    MSG_EMBD = auto()     # Worker -> parent: encoded features.
    MSG_ALIGN = auto()    # Worker -> parent: alignment results.
    MSG_READY = auto()    # Worker -> parent: ready signal.
    MSG_DONE = auto()     # Worker -> parent: stopped signal.
    MSG_ERROR = auto()    # Worker -> parent: error signal.

@dataclass
class StreamingMessage:
    """Shared encoding/alignment process messages."""
    msg_type: MsgType
    data: Any = None         # Audio chunk, embeddings, or alignment result.
    text: Optional[str] = None # Text to align.
    offset_sec: float = 0.0  # Alignment time offset.
    language: Optional[str] = None # Language.
    is_last: bool = False    # Whether this is the final audio segment.
    encode_time: float = 0.0 # Timing statistics.

@dataclass
class DecodeResult:
    """Standard decoder output."""
    text: str = ""           # Complete text including the prefix.
    new_text: str = ""       # Text generated in this call.
    stable_tokens: List[int] = field(default_factory=list)
    t_prefill: float = 0.0   # Prefill duration in milliseconds.
    t_generate: float = 0.0  # Generation duration in milliseconds.
    n_prefill: int = 0       # Prefill token count.
    n_generate: int = 0      # Generated token count.
    is_aborted: bool = False # Whether repetition or another circuit breaker interrupted generation.

@dataclass(frozen=True)
class ForcedAlignItem:
    """Alignment for one word or character."""
    text: str
    start_time: float        # Seconds.
    end_time: float          # Seconds.

@dataclass
class ForcedAlignResult:
    """Structured alignment results."""
    items: List[ForcedAlignItem]
    performance: Optional[dict] = None

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx: int) -> ForcedAlignItem:
        return self.items[idx]

@dataclass
class AlignerConfig:
    """Alignment engine settings."""
    model_dir: str
    # Separate frontend and backend.
    encoder_frontend_fn: str = "qwen3_aligner_encoder_frontend.int4.onnx"
    encoder_backend_fn: str = "qwen3_aligner_encoder_backend.int4.onnx"
    
    llm_fn: str = "qwen3_aligner_llm.q4_k.gguf" 
    onnx_provider: str = 'CPU'  # CPU, CUDA, DML, TensorRT
    llm_use_gpu: bool = True
    n_ctx: int = 2048       # Alignment uses roughly 30 tokens per second of audio plus text.
    dml_pad_to: int = 40 # Encoder padding duration.

@dataclass
class ASREngineConfig:
    """ASR engine settings."""
    model_dir: str
    encoder_frontend_fn: str = "qwen3_asr_encoder_frontend.int4.onnx"
    encoder_backend_fn: str = "qwen3_asr_encoder_backend.int4.onnx"
    llm_fn: str = "qwen3_asr_llm.q4_k.gguf"

    onnx_provider: str = 'CPU'  # CPU, CUDA, DML, TensorRT
    llm_use_gpu: bool = True
    dml_pad_to: int = 40        # Encoder padding duration for DirectML.
    n_ctx: int = 2048           # ASR uses roughly 20 tokens per second of audio plus text.
    chunk_size: float = 40.0    # A 40-second segment uses roughly 800 tokens.
    memory_num: int = 1         # One remembered and one current segment use roughly 1600 tokens.
    verbose: bool = True
    enable_aligner: bool = False
    align_config: Optional[AlignerConfig] = None

    def __post_init__(self):
        # Default encoder padding to the recognition segment duration when unspecified.
        if self.dml_pad_to is None:
            object.__setattr__(self, 'pad_to', int(self.chunk_size))
            
        if self.align_config is None:
            object.__setattr__(self, 'align_config', AlignerConfig(
                model_dir=self.model_dir,
                onnx_provider=self.onnx_provider,
                llm_use_gpu=self.llm_use_gpu,
                dml_pad_to=self.dml_pad_to # Default aligner padding to the main padding duration.
            ))
        elif self.align_config.dml_pad_to is None:
             object.__setattr__(self.align_config, 'dml_pad_to', int(self.chunk_size))

@dataclass
class TranscribeResult:
    """Transcription results with optional alignment."""
    text: str
    alignment: Optional[ForcedAlignResult] = None
    performance: Optional[dict] = None
