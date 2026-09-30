"""Gated file client with stdin cancellation; launched only by the file GUI."""

from __future__ import annotations

import os
import json
import sys
import threading
from typing import Sequence

from .events import EventSender


def _run(arguments: Sequence[str], read, app_factory, parse_command, *, emit=lambda event: None) -> int:
    """Wait for ownership, then use the normal client lifecycle and exit status."""
    # The parent attaches a kill-on-close Windows job before allowing imports.
    if read(1) != b'S':
        return 2
    emit({'type': 'progress', 'stage': 'starting'})
    stopped = threading.Event()
    finished = threading.Event()
    state = {'app': None}

    def watch_control():
        try:
            # A single cancellation byte or lost parent input requests shutdown.
            read(1)
        except OSError:
            pass
        stopped.set()
        # Cancellation can arrive while the client constructor is still running.
        while not finished.wait(0.025):
            app = state['app']
            if app is not None:
                app.stop()
                break

    # Use unbuffered os.read: no daemon owns a buffered stdin lock at exit.
    watcher = threading.Thread(target=watch_control, daemon=True, name='file-gui-control')
    watcher.start()
    try:
        command = parse_command(arguments)
        app = app_factory(command)
        state['app'] = app
        app.file_progress_callback = emit
        app.file_connect_timeout = 15.0
        # A run is an immutable snapshot, including GUI-only overrides. Closing
        # the public reloader prevents its startup poll from replacing them.
        reloader = getattr(app, 'config_reload', None)
        if reloader is not None:
            app.loop.run_until_complete(reloader.close())
        if stopped.is_set():
            app.stop()
        emit({'type': 'ready'})
        return app.start()
    finally:
        finished.set()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    options = {}
    # Duplicate the dedicated protocol pipe before redirecting all Python and
    # native console output. No transcript or arbitrary exception crosses it.
    writer = os.fdopen(os.dup(sys.stdout.fileno()), 'wb', buffering=0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(writer.fileno(), os.O_BINARY)
    with open(os.devnull, 'wb') as null:
        os.dup2(null.fileno(), sys.stdout.fileno())
    sender = EventSender(writer)
    terminal = False

    def emit(event):
        nonlocal terminal
        if event.get('type') in {'completed', 'failed'}:
            terminal = True
        sender.send(event)

    # Lazy callables preserve the gate before imports that initialize the client.
    def parse_command(values):
        nonlocal options
        if values[:1] == ['--settings-json']:
            if len(values) < 3 or len(values[1]) > 4096:
                raise ValueError
            options = json.loads(values[1])
            values = values[2:]
        # Apply only to this process, before any consumer captures Config fields.
        from config_client import ClientConfig
        from .options import DEFAULTS, validate_options
        base = {key: getattr(ClientConfig, key, default) for key, default in DEFAULTS.items()}
        options = validate_options(options, base=base)
        for key, value in options.items():
            setattr(ClientConfig, key, value)
        from core.client.cli import ClientMode, parse_client_command
        command = parse_client_command(values)
        if command.mode is not ClientMode.TRANSCRIBE:
            raise SystemExit(2)
        return command

    def app_factory(command):
        from core.client.app import CapsWriterClient
        return CapsWriterClient(command)

    try:
        # Windows CRT serializes operations on a descriptor. A blocking read on
        # fd 0 would stall library imports that inspect stdin until Cancel/EOF.
        # Give the watcher its own CRT descriptor and leave sys.stdin available.
        # The raw duplicate is process-owned: closing it from the main thread
        # while the watcher is blocked would acquire the same descriptor lock.
        descriptor = os.dup(sys.stdin.fileno())
        if os.name == 'nt':
            import msvcrt
            msvcrt.setmode(descriptor, os.O_BINARY)
        result = _run(arguments, lambda size: os.read(descriptor, size), app_factory,
                      parse_command, emit=emit)
        if result and not terminal:
            emit({'type': 'failed', 'code': 'unexpected'})
        return result
    except (ValueError, SyntaxError, SystemExit):
        if not terminal:
            emit({'type': 'failed', 'code': 'config_invalid'})
        return 2
    except Exception:
        if not terminal:
            emit({'type': 'failed', 'code': 'start_failed'})
        return 1
    finally:
        sender.close()
        writer.close()
