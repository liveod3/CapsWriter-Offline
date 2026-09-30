"""Own one file-transcription child without loading client hardware in Qt."""

from __future__ import annotations

import os
import json
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .events import EventDecoder, FRAME_LIMIT
from .options import validate_options


OUTPUT_FORMATS = frozenset({'srt', 'txt', 'json', 'merge'})
CANCEL_GRACE_MS = 6000
STARTUP_TIMEOUT_MS = 30000
SHUTDOWN_TIMEOUT_MS = 10000


def worker_command(root: Path, file: Path, formats: frozenset[str], options=None) -> list[str]:
    """Construct arguments without a shell, preserving spaces and option-like names."""
    executable = Path(sys.executable)
    if getattr(sys, 'frozen', False):
        executable = executable.with_name('start_client.exe')
        prefix = [str(executable)]
    else:
        if executable.name.casefold() == 'pythonw.exe':
            executable = executable.with_name('python.exe')
        prefix = [str(executable), str(root / 'start_client.py')]
    settings = ['--settings-json', json.dumps(options, allow_nan=False)] if options is not None else []
    return [*prefix, '--file-worker', *settings, 'transcribe', '--format',
            ','.join(sorted(formats)), '--', str(file)]


def _own_process_tree(pid: int):
    """Attach a gated Windows child before it can start an FFmpeg descendant."""
    if os.name != 'nt':
        return None
    import win32api
    import win32job

    job = win32job.CreateJobObject(None, '')
    try:
        limits = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] = (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, limits)
        handle = win32api.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            win32job.AssignProcessToJobObject(job, handle)
        finally:
            handle.Close()
        return job
    except Exception:
        job.Close()
        raise


class FileTranscriptionRunner(QObject):
    """Serialize one child with cooperative cancellation and tree cleanup.

    Call on the Qt thread. Completion reasons are stable IDs, translated by the
    window. Only validated progress frames cross stdout; the worker discards
    ordinary console output rather than retaining recognized text.
    """

    started = Signal()
    progress = Signal(dict)
    outputs = Signal(list)
    finished = Signal(bool, str)

    def __init__(self, root: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.root = Path(root).resolve()
        self._process: QProcess | None = None
        self._job = None
        self._cancelled = False
        self._start_failed = False
        self._failure = None
        self._terminal = None
        self._decoder = EventDecoder()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._force_stop)
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(lambda: self._timeout('startup_timeout'))
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(lambda: self._timeout('shutdown_timeout'))

    @property
    def active(self) -> bool:
        return self._process is not None

    def start(self, file: Path, formats: frozenset[str], options=None) -> bool:
        """Start one validated file; return False when busy or inputs are invalid."""
        if self.active:
            return False
        file = Path(file).expanduser().resolve()
        formats = frozenset(formats)
        if not file.is_file() or not formats or not formats <= OUTPUT_FORMATS:
            return False
        if options is not None:
            try:
                validate_options(options)
            except ValueError:
                return False
            # Only the child knows the effective local defaults. Preserve the
            # caller's override keys so omitted fields retain that configuration.
            options = dict(options)
        command = worker_command(self.root, file, formats, options)
        process = QProcess(self)
        self._process = process
        self._cancelled = False
        self._start_failed = False
        self._failure = None
        self._terminal = None
        self._decoder = EventDecoder()
        process.setWorkingDirectory(str(self.root))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('PYTHONIOENCODING', 'utf-8')
        environment.insert('PYTHONUNBUFFERED', '1')
        process.setProcessEnvironment(environment)
        process.setStandardErrorFile(QProcess.nullDevice())
        process.readyReadStandardOutput.connect(lambda: self._read_events(process))
        process.started.connect(lambda: self._on_started(process))
        process.finished.connect(lambda code, status: self._on_finished(process, code, status))
        process.errorOccurred.connect(lambda error: self._on_error(process, error))
        self._startup_timer.start(STARTUP_TIMEOUT_MS)
        process.start(command[0], command[1:])
        return True

    def _on_started(self, process: QProcess) -> None:
        if process is not self._process:
            return
        try:
            self._job = _own_process_tree(process.processId())
        except Exception:
            # The gated worker has not imported the client or spawned helpers.
            self._start_failed = True
            process.kill()
            return
        if self._cancelled:
            self._force_stop()
            return
        if process.write(b'S') != 1:
            self._start_failed = True
            self._force_stop()
            return
        self.started.emit()

    def _read_events(self, process: QProcess, *, final=False) -> None:
        if process is not self._process:
            return
        # Bounded reads prevent an unterminated frame from growing indefinitely.
        read_bytes = 0
        limit = FRAME_LIMIT * 64 if final else 65536
        while process.bytesAvailable() and read_bytes < limit:
            data = bytes(process.read(4096))
            if not data:
                break
            read_bytes += len(data)
            try:
                for event in self._decoder.feed(data):
                    self._accept_event(event)
            except (ValueError, UnicodeError, TypeError, RecursionError):
                self._failure = 'protocol_error'
                self._force_stop()
                return
        if process.bytesAvailable():
            if final:
                self._failure = 'protocol_error'
            else:
                QTimer.singleShot(0, lambda: self._read_events(process))
        if final:
            try:
                self._decoder.finish()
            except ValueError:
                self._failure = 'protocol_error'

    def _accept_event(self, event):
        kind = event['type']
        if kind == 'ready':
            self._startup_timer.stop()
        elif kind == 'progress':
            if event['stage'] != 'starting':
                self._startup_timer.stop()
            if self._terminal is None:
                self.progress.emit(event)
        else:
            self._startup_timer.stop()
            if self._terminal is not None:
                return
            self._terminal = event
            if kind == 'failed':
                self._failure = event['code']
            else:
                self.progress.emit(event)
                self.outputs.emit(event['output_paths'])
            self._shutdown_timer.start(SHUTDOWN_TIMEOUT_MS)

    def _timeout(self, reason):
        if self._process is not None and not self._cancelled:
            self._failure = self._failure or reason
            self._force_stop()

    def cancel(self) -> None:
        """Request existing client cleanup, escalating only after a bounded grace."""
        if self._process is None or self._cancelled:
            return
        self._cancelled = True
        self._startup_timer.stop()
        self._shutdown_timer.stop()
        # EOF also cancels; no signal is sent to an unrelated server or client.
        self._process.write(b'C')
        self._process.closeWriteChannel()
        self._timer.start(CANCEL_GRACE_MS)

    def shutdown(self) -> None:
        """Final application-exit fallback after the window has requested cancel."""
        self._cancelled = True
        self._force_stop()

    def _close_job(self) -> None:
        job, self._job = self._job, None
        if job is not None:
            job.Close()

    def _force_stop(self) -> None:
        self._close_job()
        if self._process is not None:
            self._process.kill()

    def _on_error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        if process is self._process and error == QProcess.ProcessError.FailedToStart:
            self._start_failed = True
            self._complete(process, False, 'start_failed')

    def _on_finished(self, process: QProcess, code: int,
                     status: QProcess.ExitStatus) -> None:
        if process is not self._process:
            return
        self._read_events(process, final=True)
        if self._cancelled:
            reason = 'cancelled'
        elif self._failure:
            reason = self._failure
        elif self._start_failed:
            reason = 'start_failed'
        elif (code == 0 and status == QProcess.ExitStatus.NormalExit
              and self._terminal is not None and self._terminal['type'] == 'completed'):
            reason = 'completed'
        elif code == 0 and status == QProcess.ExitStatus.NormalExit:
            reason = 'protocol_error'
        else:
            reason = 'failed'
        self._complete(process, reason == 'completed', reason)

    def _complete(self, process: QProcess, success: bool, reason: str) -> None:
        if process is not self._process:
            return
        self._timer.stop()
        self._startup_timer.stop()
        self._shutdown_timer.stop()
        self._close_job()
        self._process = None
        process.deleteLater()
        self.finished.emit(success, reason)
