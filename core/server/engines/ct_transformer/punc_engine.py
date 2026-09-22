# coding: utf-8
from typing import Any
from ..base import BasePuncEngine


class CTTransformerPuncEngine(BasePuncEngine):
    """
    Restore punctuation with sherpa-onnx CT-Transformer.
    """

    def __init__(self, model_path: str):
        super().__init__(model_path)
        self.model_path = model_path
        self.engine = None
        self._initialize()

    def _initialize(self):
        """Initialize the backend lazily."""
        import sherpa_onnx
        punc_cfg = sherpa_onnx.OfflinePunctuationConfig(
            model=sherpa_onnx.OfflinePunctuationModelConfig(
                ct_transformer=self.model_path
            ),
        )
        self.engine = sherpa_onnx.OfflinePunctuation(punc_cfg)

    def punctuate(self, text: str) -> str:
        """Insert punctuation into text."""
        if not self.engine or not text:
            return text
        try:
            return self.engine.add_punctuation(text)
        except Exception:
            return text

    def cleanup(self):
        """Release resources."""
        self.engine = None
