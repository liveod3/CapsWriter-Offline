"""Hidden client entry: RPC stays separate from native/console output."""

import os
from pathlib import Path
import sys
import threading

from .backend import Backend, safe_error
from .bridge import receive, send


def main():
    reader = sys.stdin.buffer
    original_stdout = sys.stdout
    # Preserve the pipe, then isolate both Python and native stdout from RPC.
    writer = os.fdopen(os.dup(sys.stdout.fileno()), 'wb', buffering=0)
    null = open(os.devnull, 'w', encoding='utf-8')
    os.dup2(null.fileno(), sys.stdout.fileno())
    sys.stdout = null
    request = receive(reader)
    if request.get('method') != 'boot':
        return 2
    send(writer, {'id': request['id'], 'result': True})
    root = Path.cwd()
    local = Backend(root)
    stopping = threading.Event()
    finished = threading.Event()
    writer_lock = threading.Lock()
    state = {'app': None, 'backend': None, 'error': None}

    def control():
        try:
            while not finished.is_set():
                request = receive(reader)
                try:
                    method, params = request['method'], request['params']
                    if method == 'shutdown':
                        result = None
                    elif method == 'read' and (state['error'] or state['backend'] is None
                                               or not state['app'].loop.is_running()):
                        result = local.dispatch('read', {})
                        result['worker_error'] = state['error']
                    elif state['backend'] is not None:
                        result = state['backend'].dispatch(method, params)
                    else:
                        result = local.dispatch(method, params)
                    response = {'id': request['id'], 'result': result}
                except Exception as exc:
                    response = {'id': request.get('id'), 'error': safe_error(exc)}
                with writer_lock:
                    if finished.is_set():
                        break
                    send(writer, response)
                if request.get('method') == 'shutdown':
                    # Acknowledge first, then leave stdin before interpreter teardown.
                    break
        except (EOFError, OSError, ValueError):
            pass
        finally:
            stopping.set()

    def watch_stop():
        stopping.wait()
        # Also covers a request received while the application is initializing.
        while not finished.wait(0.05):
            if state['app'] is not None:
                state['app'].stop()

    # Buffered stdin must never remain owned by a daemon during finalization.
    control_thread = threading.Thread(target=control, name='desktop-control')
    stop_thread = threading.Thread(target=watch_stop, name='desktop-stop')
    control_thread.start()
    stop_thread.start()
    try:
        from core.client.cli import ClientCommand, ClientMode
        from core.client.app import CapsWriterClient
        app = CapsWriterClient(ClientCommand(ClientMode.MIC))
        app.desktop_mode = True
        state['app'] = app
        state['backend'] = Backend(root, app.operations)
        if stopping.is_set():
            app.stop()
        # start() also owns cleanup of resources initialized by the constructor.
        # A pre-start stop must still run that cleanup on the application loop.
        app.start()
    except Exception as exc:
        state['error'] = safe_error(exc)
        # Keep reporting a controlled startup failure until the desktop acknowledges it.
        stopping.wait(timeout=20)
    finally:
        finished.set()
        stopping.set()
        with writer_lock:
            # EOF also lets the parent release stdin if the app ended on its own.
            writer.close()
        control_thread.join(timeout=2)
        stop_thread.join(timeout=2)
        sys.stdout = original_stdout
        null.close()
    return 0
