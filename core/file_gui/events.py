"""Bounded, content-free progress frames between the file worker and Qt."""

from __future__ import annotations

import json
import math
from queue import Empty, Full, Queue
import threading

from core.i18n import Notice


FRAME_LIMIT = 32768
STAGES = frozenset({'starting', 'checking', 'connecting', 'probing', 'transcribing',
                    'awaiting_result', 'saving'})
FAILURES = frozenset({'recognition_failed', 'decode_failed', 'missing_file',
                      'decoder_unavailable', 'connection_failed', 'timeout',
                      'invalid_result', 'output_failed', 'unexpected', 'config_invalid',
                      'start_failed', 'startup_timeout', 'protocol_error', 'shutdown_timeout'})
METRICS = frozenset({'processed_seconds', 'total_seconds', 'elapsed_seconds',
                     'speed', 'rtf', 'eta_seconds', 'chunks_completed',
                     'chunk_seconds', 'chunk_elapsed_seconds'})


def _invalid():
    return ValueError(Notice('gui.protocol'))


def _nonnegative_number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def validate_event(value):
    """Reject unknown fields instead of accidentally forwarding user content."""
    if not isinstance(value, dict):
        raise _invalid()
    kind = value.get('type')
    if kind == 'ready':
        allowed = {'type'}
    elif kind == 'failed':
        allowed = {'type', 'code'}
        if value.get('code') not in FAILURES:
            raise _invalid()
    elif kind in {'progress', 'completed'}:
        allowed = ({'type', 'stage', *METRICS} if kind == 'progress'
                   else {'type', 'output_paths', 'text_length', 'sequence', *METRICS})
        if kind == 'progress' and value.get('stage') not in STAGES:
            raise _invalid()
        for key in value.keys() & METRICS:
            item = value[key]
            if key == 'chunks_completed' and (type(item) is not int or not 0 <= item <= 10000000):
                raise _invalid()
            if item is None and key in {'total_seconds', 'rtf', 'eta_seconds'}:
                continue
            if not _nonnegative_number(item):
                raise _invalid()
        if kind == 'completed':
            for key, minimum in (('text_length', 0), ('sequence', 1)):
                if key in value:
                    item = value[key]
                    if type(item) is not int or item < minimum or not _nonnegative_number(item):
                        raise _invalid()
            paths = value.get('output_paths')
            if (not isinstance(paths, list) or not 1 <= len(paths) <= 4
                    or any(not isinstance(path, str) or not 0 < len(path) <= 4096
                           or '\0' in path for path in paths)):
                raise _invalid()
    else:
        raise _invalid()
    if value.keys() - allowed:
        raise _invalid()
    return value


def encode_event(value):
    data = json.dumps(validate_event(value), ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
    if len(data) > FRAME_LIMIT:
        raise _invalid()
    return data


class EventDecoder:
    def __init__(self):
        self.pending = bytearray()

    def feed(self, data):
        self.pending.extend(data)
        events = []
        while b'\n' in self.pending:
            line, _, remaining = self.pending.partition(b'\n')
            self.pending = bytearray(remaining)
            if len(line) >= FRAME_LIMIT:
                raise _invalid()
            events.append(validate_event(json.loads(line)))
        if len(self.pending) >= FRAME_LIMIT:
            raise _invalid()
        return events

    def finish(self):
        """A clean process exit cannot authorize a truncated final frame."""
        if self.pending:
            raise _invalid()


class EventSender:
    """Keep a slow parent pipe off the ASR event loop with a bounded queue."""

    def __init__(self, stream):
        self.stream = stream
        self.queue = Queue(maxsize=32)
        self.closed = False
        self.thread = threading.Thread(target=self._write, daemon=True, name='file-gui-events')
        self.thread.start()

    def send(self, event):
        if self.closed:
            return
        data = encode_event(event)
        self._enqueue(data)

    def _enqueue(self, value):
        try:
            self.queue.put_nowait(value)
        except Full:
            # Replace stale progress so final metadata always has queue space.
            try:
                self.queue.get_nowait()
            except Empty:
                pass
            self.queue.put_nowait(value)

    def _write(self):
        try:
            while True:
                data = self.queue.get()
                if data is None:
                    return
                self.stream.write(data)
                self.stream.flush()
        except (OSError, ValueError):
            self.closed = True

    def close(self):
        self.closed = True
        self._enqueue(None)
        self.thread.join(timeout=2)
