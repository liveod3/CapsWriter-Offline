# coding: utf-8
import os
import time
from typing import Optional, List, Dict, Any

from .inference.schema import ASREngineConfig, TranscriptionResult, RecognitionResult as InternalResult, DecodeResult, Statistics
from .inference.models import Models
from .inference.pipeline import InferencePipeline
from .inference.transcriber import AudioTranscriber
from ..base import BaseASREngine, RecognitionStream, EngineCapabilities, RecognitionResult
from ..language import get_language, ENGINE_FUN_ASR_NANO


class FunASRStream(RecognitionStream):
    """
    FunASR-Nano stream adapter.
    Bridge backend audio input and the standard RecognitionResult.
    """
    def __init__(self, pipeline: InferencePipeline, sample_rate: int = 16000):
        super().__init__(sample_rate)
        self.internal_stream = pipeline.create_stream()

    def accept_waveform(self, sample_rate: int, audio: Any):
        # The backend stream already implements accept_waveform.
        self.internal_stream.accept_waveform(sample_rate, audio)


class FunASREngine(BaseASREngine):
    """
    FunASR engine adapter.
    
    Provide ASR, timestamps, and punctuation.
    """

    def __init__(self, config: ASREngineConfig):
        super().__init__(config)
        # Initialize backend components.
        self.models = Models(self.config)
        self.pipeline = InferencePipeline(self.models)

    @property
    def capabilities(self) -> List[EngineCapabilities]:
        """Declare FunASR-Nano capabilities."""
        return [
            EngineCapabilities.ASR, 
            EngineCapabilities.TIMESTAMPS, 
            EngineCapabilities.PUNC
        ]

    def create_stream(self) -> FunASRStream:
        """Create an adapted recognition stream."""
        return FunASRStream(self.pipeline, sample_rate=self.config.sample_rate)

    def decode_stream(
        self,
        stream: FunASRStream,
        context: Optional[str] = None,
        language: Optional[str] = None,
        **kwargs
    ):
        """Decode a stream and copy its result."""
        # Map unified codes to FunASR's Chinese language identifiers.
        mapped_lang = get_language(ENGINE_FUN_ASR_NANO, language) if language else None
        self.pipeline.decode_stream(stream.internal_stream, context=context, language=mapped_lang)
        
        # 2. Copy the result into RecognitionResult.
        res = stream.internal_stream.result
        stream.result.text = res.text
        stream.result.tokens = list(res.tokens)
        stream.result.timestamps = list(res.timestamps)


    def cleanup(self):
        """Release resources."""
        self.models.cleanup()
