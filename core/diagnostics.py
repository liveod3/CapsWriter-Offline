"""Bounded, process-owned JSONL diagnostics; no shared multiprocess file handles."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from time import monotonic
import traceback
import uuid

from core.file_lock import file_lock


def storage_path(base, setting):
    return (Path(base) / Path(os.path.expandvars(str(setting))).expanduser()).resolve()


class DiagnosticFormatter(logging.Formatter):
    """One JSON object per event, with stable metadata and optional content."""

    def __init__(self, component, run_id):
        super().__init__()
        self.component = component
        self.run_id = run_id

    def format(self, record):
        fields = {
            "timestamp": datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "component": self.component,
            "run_id": self.run_id,
            "pid": record.process,
            "process": record.processName,
            "thread": record.threadName,
            "event": getattr(record, "event", getattr(record.msg, "message_id", "python.message")),
            "source": f"{record.filename}:{record.lineno}",
            "message": record.getMessage(),
        }
        for key in ("task_id", "socket_id", "request_id", "batch_id", "data", "content"):
            if hasattr(record, key):
                fields[key] = getattr(record, key)
        if record.exc_info:
            # Exception messages and local variables may contain private content.
            fields["exception_type"] = record.exc_info[0].__name__
            fields["traceback"] = [
                {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(fields, ensure_ascii=False, default=str)


class DiagnosticFileHandler(RotatingFileHandler):
    """Rotate one process session; clean only inactive owned session families."""

    def __init__(self, root, component, *, retention_days=30, max_bytes=10 * 1024 * 1024,
                 backup_count=5, budget_mb=200):
        self.root = Path(root).resolve() / component
        self.retention_days = max(0, int(retention_days))
        self.budget = max(1, int(budget_mb)) * 1024 * 1024
        self.run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        self.path = self.root / f"{datetime.now():%Y/%m}" / f"{component}-{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lease_path = self.path.with_suffix(".lock")
        self._lease = file_lock(self.lease_path)
        self._lease.__enter__()
        self._cleanup_at = 0.0
        self.failures = 0
        super().__init__(self.path, maxBytes=max_bytes, backupCount=backup_count,
                         encoding="utf-8", delay=True)
        self.setFormatter(DiagnosticFormatter(component, self.run_id))
        self._pattern = re.compile(
            rf"{re.escape(component)}-\d{{8}}-\d{{6}}-\d+-[a-f0-9]{{12}}\.jsonl(?:\.\d+)?$"
        )

    def cleanup(self):
        cutoff = time.time() - timedelta(days=self.retention_days).total_seconds()
        groups = {}
        for path in self.root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.jsonl*"):
            try:
                if not self._pattern.fullmatch(path.name):
                    continue
                if any(p.is_symlink() or getattr(p.stat(), "st_file_attributes", 0) & 0x400
                       for p in (path, path.parent, path.parent.parent)):
                    continue
                if not path.resolve().is_relative_to(self.root):
                    continue
                base = path.with_name(path.name.split(".jsonl")[0] + ".jsonl")
                groups.setdefault(base, []).append((path, path.stat()))
            except OSError:
                continue
        total = sum(st.st_size for items in groups.values() for _, st in items)
        for base, items in sorted(groups.items(), key=lambda pair: max(s.st_mtime for _, s in pair[1])):
            if base == self.path:
                continue
            expired = self.retention_days and max(st.st_mtime for _, st in items) < cutoff
            if not expired and total <= self.budget:
                continue
            lease = base.with_suffix(".lock")
            if lease.is_symlink():
                continue
            try:
                with file_lock(lease):
                    for path, st in items:
                        path.unlink()
                        total -= st.st_size
                lease.unlink(missing_ok=True)
            except OSError:
                continue

    def emit(self, record):
        if monotonic() >= self._cleanup_at:
            self._cleanup_at = monotonic() + 3600
            try:
                self.cleanup()
            except OSError:
                pass
        super().emit(record)

    def handleError(self, record):
        self.failures += 1
        if self.failures == 1:
            # Never use logging.handleError: it can echo a sensitive record.
            try:
                from core.i18n import tr
                sys.stderr.write(tr('logging.file_unavailable') + '\n')
            except Exception:
                pass

    def close(self):
        super().close()
        if self._lease is not None:
            self._lease.__exit__(None, None, None)
            self._lease = None
            try:
                self.lease_path.unlink(missing_ok=True)
            except OSError:
                pass


class BufferedDiagnosticHandler(logging.Handler):
    """Keep filesystem work off callers; drain for at most two seconds on exit."""

    def __init__(self, sink, capacity=256):
        super().__init__()
        self.sink = sink
        self.pending = queue.Queue(maxsize=capacity)
        self.stopping = threading.Event()
        self.dropped = 0
        self._thread = threading.Thread(target=self._write, name="diagnostic-writer", daemon=True)
        self._thread.start()

    def emit(self, record):
        if self.stopping.is_set():
            return
        try:
            self.pending.put_nowait(copy.copy(record))
        except queue.Full:
            self.dropped += 1

    def _write(self):
        try:
            while not self.stopping.is_set() or not self.pending.empty():
                try:
                    record = self.pending.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    if isinstance(record, threading.Event):
                        record.set()
                    else:
                        if self.dropped:
                            report = logging.LogRecord("diagnostics", logging.WARNING, __file__, 0,
                                                       "Diagnostic queue overflow; dropped=%s", (self.dropped,), None)
                            self.dropped = 0
                            self.sink.handle(report)
                        self.sink.handle(record)
                finally:
                    self.pending.task_done()
        finally:
            self.sink.close()

    def flush(self):
        if self.stopping.is_set() or threading.current_thread() is self._thread:
            return
        done = threading.Event()
        try:
            self.pending.put(done, timeout=0.1)
            done.wait(2.0)
        except queue.Full:
            pass

    def close(self):
        self.stopping.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        super().close()
