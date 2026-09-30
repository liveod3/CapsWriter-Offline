"""Bound directory discovery off the Qt thread without following directory links."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice
import os
from pathlib import Path
import stat
import threading

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot


MAX_FILES = 200
MAX_SCAN_ENTRIES = 20_000
MAX_SCAN_DEPTH = 32


@dataclass(frozen=True)
class ImportResult:
    """Return local selections and counts, never raw filesystem exception details.

    Skipped counts only entries actually inspected, not unvisited descendants.
    Limited means a queue, input, traversal, or depth bound was reached.
    """

    paths: tuple[Path, ...] = ()
    skipped: int = 0
    errors: int = 0
    limited: bool = False
    cancelled: bool = False


def scan_paths(
    values: Iterable[str | Path],
    existing: Iterable[str | Path],
    extensions: Iterable[str],
    recursive: bool = True,
    capacity: int = MAX_FILES,
    *,
    cancelled: Callable[[], bool] | None = None,
    max_entries: int = MAX_SCAN_ENTRIES,
    max_depth: int = MAX_SCAN_DEPTH,
) -> ImportResult:
    """Discover media in stable input/lexical order with bounded memory and work.

    All filesystem access belongs on the worker thread. Cancellation is checked
    between OS calls; a filesystem call already in progress cannot be interrupted.
    Oversized directories are omitted as a whole instead of importing a subset
    determined by the filesystem's arbitrary enumeration order.
    """
    values = tuple(islice(values, MAX_FILES + 1))
    existing = tuple(islice(existing, MAX_FILES + 1))
    capacity = max(0, min(capacity, MAX_FILES))
    remaining = max(0, capacity - len(existing))
    suffixes = {'.' + item.strip().lower().lstrip('.') for item in extensions if item.strip()}
    seen = set()
    visited = set()
    paths = []
    skipped = errors = examined = 0
    limited = len(values) > MAX_FILES
    stopped = False
    was_cancelled = False

    def stop_requested():
        nonlocal was_cancelled
        was_cancelled = was_cancelled or bool(cancelled and cancelled())
        return stopped or was_cancelled

    def visit(path, depth=0):
        nonlocal skipped, errors, examined, limited, stopped
        if stop_requested():
            return
        try:
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                # On Windows junctions are directories rather than symlinks.
                if getattr(metadata, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    skipped += 1
                    return
                if depth > max_depth:
                    skipped += 1
                    limited = True
                    return
                identity = str(path.resolve()).casefold()
                if identity in visited:
                    skipped += 1
                    return
                visited.add(identity)
                children = []
                with os.scandir(path) as entries:
                    for entry in entries:
                        if stop_requested():
                            return
                        if examined >= max_entries:
                            limited = stopped = True
                            return
                        examined += 1
                        children.append(Path(entry.path))
                children.sort(key=lambda child: (child.name.casefold(), child.name))
                for child in children:
                    if stop_requested():
                        return
                    if not recursive:
                        # The lstat in visit still rejects directory symlinks;
                        # nonrecursive discovery considers immediate files only.
                        try:
                            if child.is_dir():
                                skipped += 1
                                continue
                        except OSError:
                            errors += 1
                            skipped += 1
                            continue
                    visit(child, depth + 1)
                return
            if not stat.S_ISREG(metadata.st_mode):
                # Explicit/file symlinks may resolve to a supported file. Never
                # traverse directory links, including explicitly selected ones.
                if not stat.S_ISLNK(metadata.st_mode) or not path.is_file():
                    skipped += 1
                    return
            if path.suffix.lower() not in suffixes:
                skipped += 1
                return
            resolved = path.resolve(strict=True)
            identity = str(resolved).casefold()
            if identity in seen:
                skipped += 1
                return
            seen.add(identity)
            paths.append(resolved)
            if len(paths) >= remaining:
                limited = stopped = True
        except (OSError, ValueError, RuntimeError):
            errors += 1
            skipped += 1

    if stop_requested():
        return ImportResult(cancelled=True)
    if remaining == 0:
        return ImportResult(limited=bool(values))
    for value in existing:
        if stop_requested():
            break
        try:
            seen.add(str(Path(value).resolve()).casefold())
        except (OSError, ValueError, RuntimeError):
            # Existing queue entries were already resolved when first imported.
            seen.add(str(value).casefold())
    for value in values[:MAX_FILES]:
        if stop_requested():
            break
        try:
            path = Path(value).expanduser()
            if not path.is_absolute():
                skipped += 1
                continue
            visit(path)
        except (OSError, ValueError, RuntimeError, TypeError):
            errors += 1
            skipped += 1
    return ImportResult(tuple(paths), skipped, errors, limited, was_cancelled)


class _ImportThread(QThread):
    def __init__(self, arguments, parent):
        super().__init__(parent)
        self.arguments = arguments
        self.cancellation = threading.Event()
        self.result = ImportResult()

    def run(self):
        try:
            self.result = scan_paths(**self.arguments, cancelled=self.cancellation.is_set)
        except Exception:
            # No raw exception or filesystem content crosses the UI boundary.
            self.result = ImportResult(errors=1, cancelled=self.cancellation.is_set())


class FileImportWorker(QObject):
    """Own one scan; emit completion on Qt only after its thread has stopped.

    Keep this object alive until finished after calling cancel. Window shutdown
    can await that signal asynchronously rather than waiting on filesystem I/O.
    """

    finished = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None

    @property
    def active(self):
        return self._thread is not None

    def start(self, values, existing, extensions, recursive=True, capacity=MAX_FILES):
        if self.active:
            return False
        self._thread = _ImportThread({
            'values': tuple(islice(values, MAX_FILES + 1)),
            'existing': tuple(islice(existing, MAX_FILES + 1)),
            'extensions': frozenset(extensions),
            'recursive': recursive,
            'capacity': capacity,
        }, self)
        self._thread.finished.connect(self._complete)
        self._thread.start()
        return True

    def cancel(self):
        if self._thread is not None:
            self._thread.cancellation.set()

    @Slot()
    def _complete(self):
        thread = self._thread
        # QThread.finished precedes thread-local cleanup. Poll without blocking
        # Qt so callers can safely destroy the worker from our finished signal.
        if not thread.wait(0):
            QTimer.singleShot(10, self._complete)
            return
        self._thread = None
        result = thread.result
        thread.deleteLater()
        self.finished.emit(result)
