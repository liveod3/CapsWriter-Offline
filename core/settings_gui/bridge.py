"""Bounded JSON pipes to one owned Qt child; no listener or extra application owner."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from core.i18n import Notice
from .backend import Backend, safe_error


LIMIT = 2 * 1024 * 1024


def receive(stream):
    line = stream.readline(LIMIT + 1)
    if not line:
        raise EOFError
    if len(line) > LIMIT or not line.endswith(b'\n'):
        raise ValueError(Notice('gui.protocol'))
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError(Notice('gui.protocol'))
    return value


def send(stream, value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
    if len(data) > LIMIT:
        raise ValueError(Notice('gui.protocol'))
    stream.write(data)
    stream.flush()


class PipeBackend:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.sequence = 0

    def dispatch(self, method, params):
        self.sequence += 1
        try:
            send(self.writer, {'id': self.sequence, 'method': method, 'params': params})
            response = receive(self.reader)
        except (OSError, EOFError) as exc:
            raise Disconnected from exc
        if response.get('id') != self.sequence:
            raise ValueError(Notice('gui.protocol'))
        if response.get('error'):
            # Only the parent's controlled, sanitized display string crosses this boundary.
            raise RemoteError(response['error'])
        return response['result']


class RemoteError(Exception):
    """Sanitized parent-side error, safe for display without reconstructing exceptions."""


class Disconnected(Exception):
    """The owning client exited; the child must close without a modal prompt."""


class SettingsProcess:
    def __init__(self, root, operations):
        self.root = Path(root)
        self.backend = Backend(root, operations)
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.activate = threading.Event()
        self.process = None
        self.thread = None

    def open(self):
        """Run off shortcut/tray callbacks; serialize opening against shutdown."""
        with self.lock:
            if self.stopped.is_set():
                return
            if self.process is not None and self.process.poll() is None:
                self.activate.set()
                return
            command = [sys.executable]
            if not getattr(sys, 'frozen', False):
                command.append(str(self.root / 'start_client.py'))
            command.append('--settings-child')
            self.process = subprocess.Popen(
                command, cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            self.thread = threading.Thread(target=self._serve, args=(self.process,),
                                           name='settings-pipe', daemon=True)
            self.thread.start()

    def _serve(self, process):
        try:
            while not self.stopped.is_set():
                request = receive(process.stdout)
                identifier = request.get('id')
                try:
                    if not isinstance(identifier, int) or not isinstance(request.get('params'), dict):
                        raise ValueError(Notice('gui.protocol'))
                    result = self.backend.dispatch(request.get('method'), request['params'])
                    if request['method'] == 'read':
                        result['activate'] = self.activate.is_set()
                        self.activate.clear()
                    response = {'id': identifier, 'result': result}
                except Exception as exc:
                    response = {'id': identifier, 'error': safe_error(exc)}
                try:
                    send(process.stdin, response)
                except ValueError as exc:
                    send(process.stdin, {'id': identifier, 'error': safe_error(exc)})
        except (EOFError, OSError, ValueError):
            pass
        finally:
            # A malformed/disconnected child cannot keep an orphan window alive.
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            process.stdin.close()
            process.stdout.close()

    def stop(self):
        self.stopped.set()
        with self.lock:
            process, thread = self.process, self.thread
            if process is not None and process.poll() is None:
                # Terminate only our settings process. All saves are atomic replacements.
                process.terminate()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
