"""Keep status per task so old tasks cannot clear a newer task's indicator."""

from __future__ import annotations

from core.i18n import Notice, tr

import threading
import time

from . import logger


class ProcessingStatus:
    def __init__(self, render):
        self._render = render
        self._lock = threading.Lock()
        self._tasks = {}
        self._closed = False
        self._displayed = ""

    def begin(self, task_id: str):
        with self._lock:
            if self._closed or task_id in self._tasks:
                return
            if len(self._tasks) >= 64:
                self._tasks.pop(next(iter(self._tasks)))
            self._tasks[task_id] = ('status.transcribing', time.monotonic())
            self._publish()

    def update(self, task_id: str, text: str):
        with self._lock:
            if self._closed or task_id not in self._tasks:
                return
            previous, started = self._tasks[task_id]
            if text != previous:
                logger.info(Notice('diagnostic.processing_status.dictation_stage_task_previous_elapsed_ms_next'),
                            task_id[:8], previous, int((time.monotonic() - started) * 1000), text)
                self._tasks[task_id] = (text, time.monotonic())
                self._publish()

    def finish(self, task_id: str):
        with self._lock:
            item = self._tasks.pop(task_id, None)
            if item:
                logger.info(Notice('diagnostic.processing_status.dictation_stage_ended_task_stage_elapsed_ms'),
                            task_id[:8], item[0], int((time.monotonic() - item[1]) * 1000))
                self._publish()

    def clear(self):
        with self._lock:
            self._tasks.clear()
            self._publish()

    def close(self):
        with self._lock:
            self._closed = True
            self._tasks.clear()
            self._publish()

    def _publish(self):
        # render only enqueues UI work; enqueue under the lock to preserve thread ordering.
        text = next(reversed(self._tasks.values()))[0] if self._tasks else ""
        if text != self._displayed:
            self._displayed = text
            self._render(tr(text) if text.startswith("status.") else text)
