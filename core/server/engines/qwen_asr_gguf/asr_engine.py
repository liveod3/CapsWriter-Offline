# coding=utf-8
import os
import numpy as np
from typing import Optional, List
from .inference.asr import QwenASREngine as QwenInternalEngine
from .inference.schema import ASREngineConfig, MsgType, StreamingMessage
from ..base import BaseASREngine, RecognitionStream, EngineCapabilities, RecognitionResult
from ..language import get_language, ENGINE_QWEN_ASR


class QwenASRStream(RecognitionStream):
    """Qwen-ASR stream state."""
    def __init__(self, sample_rate=16000):
        super().__init__(sample_rate)
        self.audio_data = None

    def accept_waveform(self, sample_rate, audio):
        self.sample_rate = sample_rate
        self.audio_data = audio.astype(np.float32)


class QwenASREngine(BaseASREngine):
    """Adapt Qwen-ASR to the shared ASR engine interface."""

    def __init__(self, config: ASREngineConfig):
        super().__init__(config)
        self.engine = QwenInternalEngine(config)

    @property
    def capabilities(self) -> List[EngineCapabilities]:
        """Declare supported capabilities."""
        return [
            EngineCapabilities.ASR, 
            EngineCapabilities.PUNC
        ]

    def create_stream(self) -> QwenASRStream:
        """Create a recognition stream."""
        return QwenASRStream()

    def decode_stream(
        self, 
        stream: QwenASRStream, 
        context: Optional[str] = None,
        language: Optional[str] = None,
        temperature: float = 0.4,
        **kwargs
    ):
        """
        Decode a recognition stream.
        """
        if stream.audio_data is None:
            return

        sr = 16000
        audio_data = stream.audio_data
        
        # Truncate audio beyond the maximum duration.
        max_samples = int(self.config.chunk_size * sr)
        if len(audio_data) > max_samples:
            audio_data = audio_data[:max_samples]

        # 1. Encode synchronously.
        audio_embd, enc_time = self.engine.encoder.encode(audio_data)
        
        # 3. Build the prompt using Qwen3's English language name.
        mapped_lang = get_language(ENGINE_QWEN_ASR, language) if language else None
        full_embd = self.engine._build_prompt_embd(
            audio_embd=audio_embd,
            prefix_text="", # prefix_text is prior assistant output; streaming segments use an empty value or context.
            context=context,
            language=mapped_lang
        )

        # 4. Decode.
        res = self.engine._safe_decode(
            full_embd, 
            prefix_text="", 
            rollback_num=5, 
            is_last_chunk=True, 
            temperature=temperature, 
            streaming=False, 
        )

        # 5. Update results.
        stream.result.text = res.text
        # Qwen ASR has no token timestamps; the server pipeline supplies alignment when requested.


    def cleanup(self):
        """Release resources."""
        self.engine.shutdown()

