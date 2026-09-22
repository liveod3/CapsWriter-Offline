# coding: utf-8
"""
Token utilities.

Provide basic transformations for recognition tokens.
"""

from core.i18n import Notice

from typing import List, Tuple
from core.constants import Punctuation
from . import logger



def process_tokens_safely(tokens: List) -> List[str]:
    """
    Filter invalid UTF-8 while normalizing tokens.
    
    Args:
        tokens: Original token list.
        
    Returns:
        Cleaned string tokens.
    """
    clean_tokens = []
    for token in tokens:
        if isinstance(token, bytes):
            token = token.decode('utf-8', errors='ignore')
        clean_tokens.append(token)
    return clean_tokens


def tokens_to_text(tokens: List[str]) -> str:
    """
    Serialize tokens into display text.
    
    Handle Paraformer @@ continuation markers by joining the next token directly.
    Other engines, including Fun-ASR-Nano, usually include spaces in their tokens.
    
    Args:
        tokens: Token list.
        
    Returns:
        Complete merged text.
    """
    return "".join(tokens).replace('@@', '')


def remove_trailing_punctuation(
    tokens: List[str], 
    timestamps: List[float]
) -> Tuple[List[str], List[float]]:
    """
    Remove trailing punctuation.
    
    Remove redundant punctuation produced at segment boundaries.
    
    Args:
        tokens: Token list.
        timestamps: Timestamp list.
        
    Returns:
        Tuple of processed tokens and timestamps.
    """
    if tokens and tokens[-1] in Punctuation.ALL:
        logger.debug(Notice('diagnostic.utils.removed_trailing_punctuation'))
        return tokens[:-1], timestamps[:-1] if timestamps else timestamps
    return tokens, timestamps
