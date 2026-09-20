"""A bounded, recording-owned bridge from PortAudio to the asyncio consumer."""

import asyncio
from collections import deque
from threading import Lock


class CaptureSession:
    # 200 normal 50 ms blocks = 10 seconds of pending audio. Control events
    # have a reserved slot so a full buffer cannot prevent finish/cancel.
    MAX_PENDING_BLOCKS = 200

    def __init__(self, loop, start_time, target_window, *, max_blocks=MAX_PENDING_BLOCKS):
        self._loop = loop
        self._lock = Lock()
        self._ready = asyncio.Event()
        self._notified = False
        self._closed = False
        self._max_blocks = max_blocks
        self._audio_blocks = 0
        self._events = deque([{
            'type': 'begin', 'time': start_time, 'target_window': target_window,
        }])

    def _notify_locked(self):
        if not self._notified:
            try:
                self._loop.call_soon_threadsafe(self._ready.set)
                self._notified = True
            except RuntimeError:
                # Shutdown may close the loop between the callback and submission.
                self._closed = True
                self._events.clear()
                self._audio_blocks = 0

    def push_audio(self, data, timestamp):
        """Copy one block without I/O; abort on overflow instead of losing words."""
        with self._lock:
            if self._closed:
                return False
            if self._audio_blocks >= self._max_blocks:
                self._closed = True
                self._events.clear()
                self._audio_blocks = 0
                self._events.append({'type': 'overflow'})
                self._notify_locked()
                return False
            self._events.append({'type': 'data', 'time': timestamp, 'data': data.copy()})
            self._audio_blocks += 1
            self._notify_locked()
            return True

    def finish(self):
        """Stop accepting audio and preserve the queued tail before completion."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._events.append({'type': 'finish'})
            self._notify_locked()

    def cancel(self):
        """Release queued audio and wake a consumer waiting for its next block."""
        with self._lock:
            self._closed = True
            self._events.clear()
            self._audio_blocks = 0
            self._events.append({'type': 'cancel'})
            self._notify_locked()

    async def get(self):
        """Wait on the owning event loop; producers never wait for this consumer."""
        while True:
            with self._lock:
                if self._events:
                    event = self._events.popleft()
                    if event['type'] == 'data':
                        self._audio_blocks -= 1
                    return event
                self._ready.clear()
                self._notified = False
            await self._ready.wait()
