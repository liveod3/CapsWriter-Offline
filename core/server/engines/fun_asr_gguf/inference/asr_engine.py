import os
import time
from typing import Optional, List, Dict, Any

from .schema import ASREngineConfig, TranscriptionResult, RecognitionStream, DecodeResult, Statistics
from .models import Models
from .pipeline import InferencePipeline
from .transcriber import AudioTranscriber

class FunASREngine:
    """FunASR inference facade."""

    def __init__(self, config: ASREngineConfig):
        # Collect configuration.
        self.config = config

        # Initialize backend components.
        self.models = Models(self.config)

        # Delegate core methods directly.
        self.pipeline = InferencePipeline(self.models)
        self.create_stream = self.pipeline.create_stream
        self.decode_stream = self.pipeline.decode_stream
        
        # Create the file transcription helper.
        self.transcriber = AudioTranscriber(self.pipeline, self.config.sample_rate)
        self.transcribe = self.transcriber.transcribe



    def cleanup(self):
        """Release resources."""
        self.models.cleanup()
