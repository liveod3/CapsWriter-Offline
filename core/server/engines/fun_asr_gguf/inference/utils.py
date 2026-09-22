"""
FunASR GGUF utilities.
"""

import time
from typing import Any, Callable, Tuple

from . import logger
from core.i18n import localize_notice

def timer(func: Callable, *args, **kwargs) -> Tuple[Any, float]:
    """
    Run a function and return its result and elapsed seconds.
    Usage:
        result, elapsed = timer(my_func, arg1, arg2, kwarg=val)
    """
    start = time.perf_counter()
    res = func(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return res, elapsed

def vprint(message: str, verbose: bool = True):
    """Log each message and display it only when verbose is enabled."""
    if verbose:
        print(localize_notice(message))
    # Always retain diagnostic messages in the logger.
    logger.info(message)

def format_ms(seconds: float) -> str:
    """Format seconds as milliseconds."""
    return f"{seconds * 1000:5.0f}ms"
