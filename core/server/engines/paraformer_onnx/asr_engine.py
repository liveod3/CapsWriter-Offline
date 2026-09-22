# coding: utf-8

from core.i18n import Notice
import sherpa_onnx
import numpy as np
from typing import Optional, List, Any, Tuple
from dataclasses import dataclass
from ..base import BaseASREngine, RecognitionStream, EngineCapabilities, RecognitionResult
from core import get_logger

logger = get_logger('server')


@dataclass
class ParaformerConfig:
    """Paraformer engine settings."""
    paraformer: str
    tokens: str
    num_threads: int = 4
    sample_rate: int = 16000
    feature_dim: int = 80
    decoding_method: str = 'greedy_search'
    provider: str = 'cpu'
    debug: bool = False


class ParaformerStream(RecognitionStream):
    """
    Paraformer stream adapter.
    Forward to sherpa_onnx.OfflineStream and expose standard results.
    """
    def __init__(self, recognizer: sherpa_onnx.OfflineRecognizer, sample_rate: int = 16000):
        super().__init__(sample_rate)
        # Create the underlying sherpa-onnx stream.
        self.internal_stream = recognizer.create_stream()

    def accept_waveform(self, sample_rate: int, audio: np.ndarray):
        self.internal_stream.accept_waveform(sample_rate, audio.astype(np.float32))


class ParaformerEngine(BaseASREngine):
    """
    Paraformer engine adapter.

    Capabilities: ASR and timestamps.
    Native punctuation is unavailable.
    """

    @staticmethod
    def _is_punct(ch: str) -> bool:
        return ch in '，。？！、,.?!:；、'

    @staticmethod
    def _post_process_tokens(tokens: List[str], timestamps: List[float]) -> Tuple[List[str], List[float]]:
        """
        Merge BPE pieces into word tokens compatible with the shared result format.

        Paraformer marks continuation pieces with @@.
        Processing steps:
        1. Merge BPE pieces into complete words.
        2. Join consecutive single ASCII letters in spelled words, such as a s -> as.
        3. Insert space tokens at language boundaries:
           - English to English: space.
           - English to non-ASCII text: space.
           - Non-ASCII to non-ASCII text: no space.
           - Next to punctuation: no space.
        """
        # Phase 1: merge BPE pieces and consecutive ASCII letters.
        merged: List[Tuple[str, float, bool]] = []  # (text, ts, is_english)
        bpe_parts: List[str] = []
        bpe_ts: Optional[float] = None
        ascii_buf: List[str] = []
        ascii_ts: Optional[float] = None

        def flush_ascii():
            nonlocal ascii_ts
            if ascii_buf:
                merged.append((''.join(ascii_buf), ascii_ts or 0.0, True))
                ascii_buf.clear()
                ascii_ts = None

        for token, ts in zip(tokens, timestamps):
            if token.endswith('@@'):
                flush_ascii()
                bpe_parts.append(token[:-2])
                if bpe_ts is None:
                    bpe_ts = ts
            else:
                if bpe_parts:
                    flush_ascii()
                    word = ''.join(bpe_parts) + token
                    merged.append((word, bpe_ts or ts, True))
                    bpe_parts.clear()
                    bpe_ts = None
                elif len(token) == 1 and token.isascii() and token.isalpha():
                    if ascii_ts is None:
                        ascii_ts = ts
                    ascii_buf.append(token)
                else:
                    flush_ascii()
                    is_eng = bool(token and token[0].isascii() and token[0].isalpha())
                    merged.append((token, ts, is_eng))

        flush_ascii()
        if bpe_parts:
            merged.append((''.join(bpe_parts), bpe_ts or 0.0, True))

        # Phase 2: insert spaces at language boundaries.
        result_tokens: List[str] = []
        result_timestamps: List[float] = []
        for i, (text, ts, is_eng) in enumerate(merged):
            if i > 0:
                prev_text, _, prev_eng = merged[i - 1]
                need_space = False
                if is_eng and prev_eng:
                    need_space = True               # English + English
                elif is_eng and not prev_eng and not ParaformerEngine._is_punct(prev_text):
                    need_space = True               # CJK + English
                elif not is_eng and prev_eng and not ParaformerEngine._is_punct(text):
                    need_space = True               # English + CJK
                if need_space:
                    result_tokens.append(' ')
                    result_timestamps.append(ts)    # Use the following word's timestamp.
            result_tokens.append(text)
            result_timestamps.append(ts)

        return result_tokens, result_timestamps

    def __init__(self, config: ParaformerConfig):
        super().__init__(config)
        logger.debug(Notice('diagnostic.asr_engine.initializing_paraformerengine_with_configuration', value0=self.config))
        
        # Extract sherpa-onnx arguments.
        params = {
            'paraformer': self.config.paraformer,
            'tokens': self.config.tokens,
            'num_threads': self.config.num_threads,
            'sample_rate': self.config.sample_rate,
            'feature_dim': self.config.feature_dim,
            'decoding_method': self.config.decoding_method,
            'provider': self.config.provider,
            'debug': self.config.debug,
        }
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(**params)

    @property
    def capabilities(self) -> List[EngineCapabilities]:
        """Declare supported capabilities."""
        return [
            EngineCapabilities.ASR, 
            EngineCapabilities.TIMESTAMPS
        ]

    def create_stream(self) -> ParaformerStream:
        """Create an adapted recognition stream."""
        return ParaformerStream(self.recognizer, sample_rate=self.config.sample_rate)

    def decode_stream(
        self,
        stream: ParaformerStream,
        context: Optional[str] = None,
        language: Optional[str] = None,
        **kwargs
    ):
        """Decode a stream and copy its result."""
        if context:
            logger.debug(Notice('diagnostic.asr_engine.paraformerengine_does_not_support_decoding_context_ignored'))
        if language and language != 'auto':
            logger.debug(Notice('diagnostic.asr_engine.paraformer_language_override_ignored'))
        
        # 1. Decode through the backend.
        self.recognizer.decode_stream(stream.internal_stream)
        
        # 2. Copy sherpa-onnx results into the standard result structure.
        res = stream.internal_stream.result
        stream.result.text = res.text
        # Merge BPE pieces into words and keep spaces as separate tokens.
        stream.result.tokens, stream.result.timestamps = self._post_process_tokens(
            list(res.tokens), list(res.timestamps)
        )


    def cleanup(self):
        """Release resources."""
        self.recognizer = None
