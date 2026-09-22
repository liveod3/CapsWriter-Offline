# coding: utf-8
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Any
import numpy as np


class EngineCapabilities(Enum):
    """Declare engine capabilities."""
    ASR = auto()            # Basic speech recognition.
    PUNC = auto()           # Native punctuation.
    TIMESTAMPS = auto()     # Native timestamps.
    STREAMING = auto()      # Native streaming inference.


@dataclass
class RecognitionResult:
    """Standard recognition result."""
    text: str = ""
    tokens: List[str] = field(default_factory=list)
    timestamps: List[float] = field(default_factory=list)
    language: Optional[str] = None
    duration: float = 0.0
    performance: dict = field(default_factory=dict)


class RecognitionStream(ABC):
    """
    Standard recognition stream interface.
    Streaming engines can implement this directly; segment-based engines adapt their input.
    """
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.result = RecognitionResult()

    @abstractmethod
    def accept_waveform(self, sample_rate: int, audio: np.ndarray):
        """Accept an audio chunk."""
        pass


class BaseASREngine(ABC):
    """
    Speech recognition engine interface.
    
    All ASR engines implement this interface, including SenseVoice, Paraformer, and Qwen.
    """

    def __init__(self, config: Any):
        self.config = config

    @property
    @abstractmethod
    def capabilities(self) -> List[EngineCapabilities]:
        """Declare supported capabilities."""
        pass

    @abstractmethod
    def create_stream(self) -> RecognitionStream:
        """Create a recognition stream."""
        pass

    @abstractmethod
    def decode_stream(
        self, 
        stream: RecognitionStream, 
        context: Optional[str] = None,
        **kwargs
    ):
        """Run inference and update stream.result."""
        pass


    @abstractmethod
    def cleanup(self):
        """Release model resources."""
        pass


class BasePuncEngine(ABC):
    """
    Punctuation engine interface.
    """

    def __init__(self, config: Any):
        self.config = config

    def punctuate(self, text: str) -> str:
        """Insert or correct punctuation; the default returns the input unchanged."""
        return text

    def cleanup(self):
        """Release resources."""
        pass


class BaseAlignEngine(ABC):
    """
    Forced alignment engine interface.
    """

    def __init__(self, config: Any):
        self.config = config

    def align(self, audio: np.ndarray, text: str, **kwargs) -> Any:
        """Align audio and text; the default returns None."""
        return None

    def cleanup(self):
        """Release resources."""
        pass
