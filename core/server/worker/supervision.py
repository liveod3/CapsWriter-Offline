"""Bound progress checks without waiting forever on a dead process's lock."""

import time

from ..delivery import ResultDeliveryError


def progress(clock, *, update=False):
    if clock is None:
        return time.monotonic()
    lock = clock.get_lock()
    if not lock.acquire(timeout=0.1):
        raise ResultDeliveryError('WorkerProgressUnavailable')
    try:
        if update:
            clock.value = time.monotonic()
        return clock.value
    finally:
        lock.release()
