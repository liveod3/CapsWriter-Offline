# coding=utf-8
import numpy as np
from typing import List, Optional

from .inference.aligner import QwenForcedAligner as InternalAligner
from .inference.schema import AlignerConfig, ForcedAlignResult
from ..base import BaseAlignEngine
from ..language import get_language, ENGINE_ALIGNER


class QwenForceAligner(BaseAlignEngine):
    """
    Qwen forced aligner adapter.
    
    Align recognized text with original audio through the backend
    to supply token timestamps.
    """

    def __init__(self, config: AlignerConfig):
        super().__init__(config)
        self.engine = InternalAligner(config)

    def align(
        self,
        audio: np.ndarray,
        text: str,
        language: Optional[str] = None,
        offset_sec: float = 0.0,
        **kwargs
    ) -> ForcedAlignResult:
        """
        Run forced alignment.
        """
        if not text:
            return None

        # Map the unified language code to an English aligner name; default to Chinese.
        mapped = get_language(ENGINE_ALIGNER, language) if language else None

        return self.engine.align(
            audio=audio,
            text=text,
            language=mapped or "Chinese",
            offset_sec=offset_sec
        )

    def cleanup(self):
        """Release resources."""
        if hasattr(self.engine, 'ctx'):
            del self.engine.ctx
        if hasattr(self.engine, 'model'):
            del self.engine.model
        self.engine = None
