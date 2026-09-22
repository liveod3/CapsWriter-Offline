# coding: utf-8
"""
Text and token merging package.

Merge recognition results using text or timestamp strategies.
"""

from .. import logger

from .text_merger import merge_by_text
from .token_merger import merge_tokens_by_sequence_matcher
from .utils import (
    process_tokens_safely,
    tokens_to_text,
    remove_trailing_punctuation
)

__all__ = [
    'merge_by_text',
    'merge_tokens_by_sequence_matcher',
    'process_tokens_safely',
    'tokens_to_text',
    'remove_trailing_punctuation',
]
