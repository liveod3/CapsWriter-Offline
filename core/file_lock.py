"""Small cross-process file locks for owned archives and append-only records."""

import os
import time
from time import monotonic
from contextlib import contextmanager


@contextmanager
def file_lock(path, *, timeout=0.0):
    """Lock one byte without truncation; callers own the lock-file lifetime."""
    stream = open(path, "a+b")
    acquired = False
    try:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        deadline = monotonic() + timeout
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if monotonic() >= deadline:
                    raise TimeoutError("Archive file is busy") from None
                time.sleep(0.01)
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()
