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
    将语言名称归一化为 Qwen3-ASR 使用的标准格式：
    首字母大写，其余小写（例如 'cHINese' -> 'Chinese'）。
    """
    if language is None:
        raise ValueError(Notice('validation.utils.language_is_none'))
    s = str(language).strip()
    if not s:
        raise ValueError(Notice('validation.utils.language_is_empty'))
    return s[:1].upper() + s[1:].lower()

def validate_language(language: str) -> None:
    """
    验证语言是否在支持列表中。
    """
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(Notice('validation.utils.unsupported_language_supported', value0=language, value1=SUPPORTED_LANGUAGES))

