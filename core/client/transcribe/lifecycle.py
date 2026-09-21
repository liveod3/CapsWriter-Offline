"""Own media subprocesses through cancellation and bounded pipe cleanup."""

import asyncio
import math


PROCESS_GRACE_SECONDS = 2.0
PROCESS_KILL_SECONDS = 3.0
PROBE_TIMEOUT_SECONDS = 15.0


def positive_timeout(config, name, default):
    value = getattr(config, name, default)
    if isinstance(value, bool):
        return default
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) and value > 0 else default


async def complete_cleanup(operation):
    """Finish one owned cleanup task even if its caller is canceled repeatedly."""
    task = asyncio.create_task(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def reap_process(process):
    """Terminate, drain pipes and reap; escalate once if graceful exit stalls."""
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    # Draining matters even after exit: a full stdout pipe can delay wait().
    drain = asyncio.create_task(process.communicate())
    try:
        try:
            await asyncio.wait_for(asyncio.shield(drain), PROCESS_GRACE_SECONDS)
        except asyncio.TimeoutError:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(asyncio.shield(drain), PROCESS_KILL_SECONDS)
        await asyncio.wait_for(process.wait(), PROCESS_KILL_SECONDS)
    finally:
        if not drain.done():
            drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)


async def open_process(*args, **kwargs):
    """Retain a child created just as its owning operation is canceled."""
    opening = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
    try:
        return await asyncio.shield(opening)
    except asyncio.CancelledError:
        async def cleanup_late_child():
            process = await opening
            await reap_process(process)

        await complete_cleanup(cleanup_late_child())
        raise
