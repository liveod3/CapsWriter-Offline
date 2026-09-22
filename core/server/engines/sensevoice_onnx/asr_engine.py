# coding=utf-8
import numpy as np
from typing import Optional, List
from .inference.engine import SenseVoiceInference
from .inference.schema import ASREngineConfig as SenseVoiceConfig
from ..base import BaseASREngine, RecognitionStream, EngineCapabilities, RecognitionResult
from ..language import get_language, ENGINE_SENSEVOICE


class SenseVoiceStream(RecognitionStream):
    """SenseVoice stream state."""
    def __init__(self, sample_rate=16000):
        super().__init__(sample_rate)
        self.audio_data = None

    def accept_waveform(self, sample_rate, audio):
        self.sample_rate = sample_rate
        self.audio_data = audio.astype(np.float32)


class SenseVoiceEngine(BaseASREngine):
    """SenseVoice engine adapter."""

    def __init__(self, config: SenseVoiceConfig):
        super().__init__(config)
        self.engine = SenseVoiceInference(config)

    @property
    def capabilities(self) -> List[EngineCapabilities]:
        """Declare SenseVoice capabilities."""
        return [
            EngineCapabilities.ASR, 
            EngineCapabilities.PUNC, 
            EngineCapabilities.TIMESTAMPS
        ]

    def create_stream(self) -> SenseVoiceStream:
        """Create a recognition stream."""
        return SenseVoiceStream()

    def decode_stream(
        self, 
        stream: SenseVoiceStream, 
        context: Optional[str] = None,
        language: Optional[str] = None,
        itn: bool = True,
        **kwargs
    ):
        """
        Decode a recognition stream.
        """
        if stream.audio_data is None:
            return

        # Map the unified language to SenseVoice lid: auto, zh, en, ja, ko, or yue.
        lid = get_language(ENGINE_SENSEVOICE, language) if language else None
        res = self.engine.recognize(
            stream.audio_data,
            lid=lid or "auto",
            itn=itn
        )

        # Update results.
        stream.result.text = res.text
        
        # Convert backend results into tokens and timestamps for the server pipeline.
        stream.result.tokens = [r.text for r in res.results]
        stream.result.timestamps = [r.start for r in res.results]


    def cleanup(self):
        """Release resources."""
        pass

