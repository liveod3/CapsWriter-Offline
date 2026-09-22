# coding: utf-8
"""
Asynchronous thread execution.

Retain an asyncio.to_thread compatibility helper for older callers.
Run blocking functions outside the event-loop thread.
"""

import functools
import contextvars
from asyncio import events
from typing import Any, Callable, TypeVar

__all__ = ('to_thread',)

T = TypeVar('T')


async def to_thread(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """
    Run a function asynchronously in another thread.
    
    Wrap the blocking call in a coroutine using the thread executor.
    Propagate the current contextvars.Context to the new thread.
    
    Args:
        func: Function to call.
        *args: Positional arguments.
        **kwargs: Keyword arguments.
        
    Returns:
        Function return value.
        
    Example:
        >>> result = await to_thread(blocking_io_function, arg1, arg2)
    """
    loop = events.get_running_loop()
    ctx = contextvars.copy_context()
    func_call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(None, func_call)
