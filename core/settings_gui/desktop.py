"""Own the desktop client's hidden process; never start or stop an ASR server."""

import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import threading

from core.i18n import Notice, tr
from .backend import Backend
from .bridge import receive, send


class ClientSession:
    """One serialized pipe session with bounded waits and an owned Windows job."""

    def __init__(self, root):
        self.root = Path(root)
        self.process = None
        self.job = None
        self.reader = None
        self.responses = Queue(maxsize=4)
        self.sequence = 0
        self.closed = threading.Event()

    def start(self):
        executable = Path(sys.executable)
        if getattr(sys, 'frozen', False):
            executable = executable.with_name('start_client.exe')
        elif executable.name.casefold() == 'pythonw.exe':
            executable = executable.with_name('python.exe')
        command = [str(executable)]
        if not getattr(sys, 'frozen', False):
            command.append(str(self.root / 'start_client.py'))
        command.append('--desktop-worker')
        self.process = subprocess.Popen(command, cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            if os.name == 'nt':
                import win32api
                import win32job
                self.job = win32job.CreateJobObject(None, '')
                limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
                limits['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
                # The worker waits for boot before loading audio or creating helpers.
                handle = win32api.OpenProcess(0x0100 | 0x0001, False, self.process.pid)
                try:
                    win32job.AssignProcessToJobObject(self.job, handle)
                finally:
                    handle.Close()
            self.reader = threading.Thread(target=self._read, daemon=True, name='desktop-client-pipe')
            self.reader.start()
            self.call('boot', {})
        except Exception:
            self.close(graceful=False)
            raise

    def _read(self):
        try:
            while not self.closed.is_set():
                response = receive(self.process.stdout)
                self.responses.put_nowait(response)
        except Exception:
            if not self.closed.is_set():
                try:
                    self.responses.put_nowait(None)
                except Exception:
                    pass
        finally:
            # Worker output EOF can precede process exit. Release its control
            # reader so interpreter shutdown never waits on an open input pipe.
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass

    def call(self, method, params):
        self.sequence += 1
        try:
            send(self.process.stdin, {'id': self.sequence, 'method': method, 'params': params})
            response = self.responses.get(timeout=12)
            if response is None or response.get('id') != self.sequence:
                raise ValueError
        except (OSError, Empty, ValueError):
            raise RuntimeError(Notice('gui.client_lost')) from None
        if response.get('error'):
            from .bridge import RemoteError
            raise RemoteError(response['error'])
        return response['result']

    def close(self, *, graceful=True):
        process = self.process
        if process is None:
            return
        try:
            if graceful and process.poll() is None:
                try:
                    self.call('shutdown', {})
                    process.wait(timeout=15)
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    pass
        finally:
            self.closed.set()
            if self.job is not None:
                self.job.Close()
                self.job = None
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            if self.reader:
                self.reader.join(timeout=1)
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass


class DesktopBackend(Backend):
    """Start the client automatically, retain a usable window on startup failure."""

    def __init__(self, root, session_factory=ClientSession):
        super().__init__(root)
        self.factory = session_factory
        self.session = None
        self.phase = 'idle'
        self.message = ''
        self.last_snapshot = None

    def start_client(self):
        if self.phase in ('starting', 'running', 'stopping'):
            return
        self.phase = 'starting'
        self.last_snapshot = None
        self.message = tr('gui.preparing_client')
        self.session = self.factory(self.root)
        try:
            self.session.start()
        except Exception as exc:
            self.failed(exc)

    def failed(self, exc):
        from .backend import safe_error
        from .bridge import RemoteError
        self.phase = 'failed'
        self.message = tr('gui.client_failed', reason=str(exc) if isinstance(exc, RemoteError) else safe_error(exc))
        if self.session:
            self.session.close(graceful=False)
            self.session = None

    def dispatch(self, method, params):
        if method == 'input_devices':
            return super().dispatch(method, params)
        if method == 'status':
            if self.phase != 'running' or not self.session:
                return None
            try:
                return self.session.call('status', {})
            except Exception as exc:
                self.failed(exc)
                return None
        if method == 'desktop_start':
            self.start_client()
            return None
        if method == 'desktop_stop':
            self.phase = 'stopping'
            if self.session:
                self.session.close()
                self.session = None
            self.phase = 'stopped'
            return None
        if method == 'read':
            if self.phase == 'idle':
                self.start_client()
            if self.session:
                try:
                    result = self.session.call('read', {})
                    if result.get('worker_error'):
                        from .bridge import RemoteError
                        raise RemoteError(result['worker_error'])
                    self.phase = 'running' if result.get('runtime') else 'starting'
                    self.last_snapshot = result
                except Exception as exc:
                    self.failed(exc)
            result = self.last_snapshot if self.session and self.last_snapshot else super().dispatch(method, params)
            result = dict(result)
            result['desktop'] = {'phase': self.phase, 'message': self.message}
            if self.phase == 'failed':
                result['runtime'] = None
            return result
        # External file launch belongs to the GUI so closing our worker cannot
        # terminate an editor that the user is still using.
        if self.phase == 'running' and method == 'advanced':
            path = self.session.call('advanced_path', params)
            subprocess.Popen(['notepad.exe', path])
            return None
        if self.phase == 'running' and method == 'history_open':
            path = self.session.call('history_open_path', params)
            subprocess.Popen(['notepad.exe', path])
            return None
        if self.phase == 'running' and method not in ('advanced', 'history_open'):
            result = self.session.call(method, params)
            if method in ('save', 'validate') and isinstance(result, dict):
                result['runtime'] = (self.last_snapshot or {}).get('runtime')
                result['desktop'] = {'phase': self.phase, 'message': self.message}
            return result
        return super().dispatch(method, params)
