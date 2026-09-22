"""
ASR language identifier mappings.

Use lowercase English names as stable keys, such as auto, chinese, english, and japanese.
Map each key to the identifier expected by the selected engine.

Engine identifier formats:
  - SenseVoice: Short codes (auto/zh/en/ja/ko/yue).
  - Qwen3-ASR / ForceAligner: Capitalized English names (Chinese/English/Japanese/...).
  - FunASR-Nano: Chinese names; the standard model documents three languages.
  - Paraformer: No language selection for the Chinese model.
"""

from typing import Dict, Optional, List

# Unified language codes, ordered by expected usage.
# Keys are lowercase English names; engine dictionaries contain backend identifiers.

LANGUAGE_MAP: Dict[str, Dict[str, Optional[str]]] = {
    "auto": {                               # Automatic detection.
        "paraformer": None,
        "sensevoice": "auto",
        "fun_asr_nano": None,
        "qwen_asr": None,
        "aligner": None,
    },
    "chinese": {                            # Chinese.
        "paraformer": None,
        "sensevoice": "zh",
        "fun_asr_nano": "中文",
        "qwen_asr": "Chinese",
        "aligner": "Chinese",
    },
    "english": {                            # English.
        "paraformer": None,
        "sensevoice": "en",
        "fun_asr_nano": "英文",
        "qwen_asr": "English",
        "aligner": "English",
    },
    "cantonese": {                          # Cantonese.
        "paraformer": None,
        "sensevoice": "yue",
        "fun_asr_nano": None, 
        "qwen_asr": "Cantonese",
        "aligner": "Cantonese",
    },
    "japanese": {                           # Japanese.
        "paraformer": None,
        "sensevoice": "ja",
        "fun_asr_nano": "日文",
        "qwen_asr": "Japanese",
        "aligner": "Japanese",
    },
    "korean": {                             # Korean.
        "paraformer": None,
        "sensevoice": "ko",
        "fun_asr_nano": None, 
        "qwen_asr": "Korean",
        "aligner": "Korean",
    },
    # Additional Qwen3 languages.
    # Standard FunASR supports Chinese, English, and Japanese; MLT supports more languages.

    "arabic": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Arabic",
        "aligner": "Arabic",
    },
    "german": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "German",
        "aligner": "German",
    },
    "french": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "French",
        "aligner": "French",
    },
    "spanish": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Spanish",
        "aligner": "Spanish",
    },
    "portuguese": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Portuguese",
        "aligner": "Portuguese",
    },
    "indonesian": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Indonesian",
        "aligner": "Indonesian",
    },
    "italian": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Italian",
        "aligner": "Italian",
    },
    "russian": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Russian",
        "aligner": "Russian",
    },
    "thai": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Thai",
        "aligner": "Thai",
    },
    "vietnamese": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Vietnamese",
        "aligner": "Vietnamese",
    },
    "turkish": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Turkish",
        "aligner": "Turkish",
    },
    "hindi": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Hindi",
        "aligner": "Hindi",
    },
    "malay": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Malay",
        "aligner": "Malay",
    },
    "dutch": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Dutch",
        "aligner": "Dutch",
    },
    "swedish": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Swedish",
        "aligner": "Swedish",
    },
    "danish": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Danish",
        "aligner": "Danish",
    },
    "finnish": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Finnish",
        "aligner": "Finnish",
    },
    "polish": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Polish",
        "aligner": "Polish",
    },
    "czech": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Czech",
        "aligner": "Czech",
    },
    "filipino": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Filipino",
        "aligner": "Filipino",
    },
    "persian": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Persian",
        "aligner": "Persian",
    },
    "greek": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Greek",
        "aligner": "Greek",
    },
    "romanian": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Romanian",
        "aligner": "Romanian",
    },
    "hungarian": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Hungarian",
        "aligner": "Hungarian",
    },
    "macedonian": {
        "paraformer": None,
        "sensevoice": None,
        "fun_asr_nano": None,
        "qwen_asr": "Macedonian",
        "aligner": "Macedonian",
    },
}


# Engine identifiers.

ENGINE_SENSEVOICE = "sensevoice"
ENGINE_QWEN_ASR = "qwen_asr"
ENGINE_FUN_ASR_NANO = "fun_asr_nano"
ENGINE_PARAFORMER = "paraformer"
ENGINE_ALIGNER = "aligner"

ALL_ENGINES = [ENGINE_PARAFORMER, ENGINE_SENSEVOICE, ENGINE_FUN_ASR_NANO, ENGINE_QWEN_ASR, ENGINE_ALIGNER]


# Mapping helpers.

def get_language(engine: str, unified_code: str) -> Optional[str]:
    """
    Map a unified language code to an engine identifier.

    Args:
        engine: ENGINE_* constant.
        unified_code: Case-insensitive language key, such as "chinese" or "english".

    Returns:
        Engine-specific identifier, or None if unsupported.
    """
    entry = LANGUAGE_MAP.get(unified_code.lower())
    if entry is None:
        return None
    return entry.get(engine)


def supported_codes(engine: str) -> List[str]:
    """
    Return the engine's supported unified language codes.

    Args:
        engine: Engine identifier.

    Returns:
        Supported codes in LANGUAGE_MAP order.
    """
    return [code for code, entry in LANGUAGE_MAP.items() if entry.get(engine) is not None]


def validate(engine: str, unified_code: str) -> bool:
    """
    Return whether an engine supports the language code.

    Returns:
        True if supported, otherwise False.
    """
    return get_language(engine, unified_code) is not None


def list_available() -> Dict[str, List[str]]:
    """
    List supported language codes for all engines.

    Returns:
        { engine_name: [supported_codes] }
    """
    return {engine: supported_codes(engine) for engine in ALL_ENGINES}
