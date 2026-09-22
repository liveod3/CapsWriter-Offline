# coding: utf-8
"""
Internal constants.

Define shared audio format and implementation constants.
Put user settings in the root client or server configuration files.
"""


class AudioFormat:
    """Audio format constants."""
    SAMPLE_RATE: int = 16000           # Sample rate in Hz.
    BYTES_PER_SAMPLE: int = 4          # Bytes per float32 sample.
    CHANNELS: int = 1                   # Channel count (mono).
    
    # Derived properties.
    BYTES_PER_SECOND: int = SAMPLE_RATE * BYTES_PER_SAMPLE * CHANNELS  # 64000
    
    @classmethod
    def seconds_to_bytes(cls, seconds: float) -> int:
        """Convert seconds to bytes."""
        return int(seconds * cls.BYTES_PER_SECOND)
    
    @classmethod
    def bytes_to_seconds(cls, byte_count: int) -> float:
        """Convert bytes to seconds."""
        return byte_count / cls.BYTES_PER_SECOND


class Punctuation:
    """Punctuation sets."""
    # Common Chinese and English punctuation.
    ALL = '，。！？；：、「」『』（）《》【】[]{},.!?;:"\''
