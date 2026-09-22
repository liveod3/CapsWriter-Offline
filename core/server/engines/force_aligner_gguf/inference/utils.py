# coding=utf-8

from core.i18n import Notice
import numpy as np
from typing import List, Optional

SUPPORTED_LANGUAGES: List[str] = [
    "Chinese",
    "English",
    "Cantonese",
    "Arabic",
    "German",
    "French",
    "Spanish",
    "Portuguese",
    "Indonesian",
    "Italian",
    "Korean",
    "Russian",
    "Thai",
    "Vietnamese",
    "Japanese",
    "Turkish",
    "Hindi",
    "Malay",
    "Dutch",
    "Swedish",
    "Danish",
    "Finnish",
    "Polish",
    "Czech",
    "Filipino",
    "Persian",
    "Greek",
    "Romanian",
    "Hungarian",
    "Macedonian"
]

def normalize_language_name(language: str) -> str:
    """
    Normalize language names to Qwen3-ASR's format:
    capitalize the first letter and lowercase the rest, such as cHINese -> Chinese.
    """
    if language is None:
        raise ValueError(Notice('validation.utils.language_is_none'))
    s = str(language).strip()
    if not s:
        raise ValueError(Notice('validation.utils.language_is_empty'))
    return s[:1].upper() + s[1:].lower()

def validate_language(language: str) -> None:
    """
    Check whether the language is supported.
    """
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(Notice('validation.utils.unsupported_language_supported', value0=language, value1=SUPPORTED_LANGUAGES))

