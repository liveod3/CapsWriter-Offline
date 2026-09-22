"""Validate overlay thread ownership without starting Tk or touching the desktop."""

from queue import Queue
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from core.ui.status_host import StatusUIHost


def host():
    instance = object.__new__(StatusUIHost)
    instance.ui_queue = Queue()
    instance._lock = threading.Lock()
    instance._ui_closed = False
    instance.is_running = False
    instance.root = None
    return instance


def test_queue_batch_is_bounded_and_failed_callback_does_not_block_others():
    instance = host()
    instance.is_running = True
    instance.root = Mock()
    broken = Mock(side_effect=RuntimeError('private callback content'))
    instance.post_ui(broken)
    callbacks = [Mock() for _ in range(130)]
    for callback in callbacks:
        instance.post_ui(callback)
    instance._process_queue()
    assert sum(callback.call_count for callback in callbacks) == 127
    instance.root.after.assert_called_once_with(100, instance._process_queue)
    instance._process_queue()
    assert all(callback.call_count == 1 for callback in callbacks)
    instance.post_ui(Mock())
    instance._on_close()
    instance.post_ui(Mock())
    assert instance.ui_queue.empty()


def test_startup_failure_discards_pending_work_and_rejects_late_submissions(monkeypatch):
    instance = host()
    callback = Mock()
    instance.post_ui(callback)
    monkeypatch.setattr('core.ui.status_host.tk.Tk', Mock(side_effect=RuntimeError('private path')))
    instance._run()
    instance.post_ui(callback)
    assert instance._ui_closed and not instance.is_running and instance.ui_queue.empty()
    callback.assert_not_called()


def test_tk_creation_callbacks_quit_and_destroy_stay_on_one_owner_thread(monkeypatch):
    instance = host()
    calls = []
    owner = []

    def mark(name):
        calls.append((name, threading.get_ident()))

    def make_root():
        owner.append(threading.get_ident())
        root = Mock()
        root.withdraw.side_effect = lambda: mark('withdraw')
        root.mainloop.side_effect = lambda: mark('mainloop')
        root.quit.side_effect = lambda: mark('quit')
        root.destroy.side_effect = lambda: mark('destroy')
        return root

    monkeypatch.setattr('core.ui.status_host.tk.Tk', make_root)
    instance.post_ui(lambda _: mark('callback'))
    thread = threading.Thread(target=instance._run)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert [name for name, _ in calls] == ['withdraw', 'callback', 'mainloop', 'quit', 'destroy']
    assert {identifier for _, identifier in calls} == set(owner)
    assert owner[0] != threading.get_ident()
    assert instance.root is None and instance._ui_closed


def test_concurrent_first_use_starts_one_host_thread(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(StatusUIHost, '_instance', None)
    factory = Mock()
    monkeypatch.setattr('core.ui.status_host.threading', SimpleNamespace(Thread=factory))
    with ThreadPoolExecutor(max_workers=4) as pool:
        instances = list(pool.map(lambda _: StatusUIHost(), range(20)))
    assert all(instance is instances[0] for instance in instances)
    factory.assert_called_once()
    factory.return_value.start.assert_called_once()


def test_ui_imports_without_legacy_html_rendering_dependencies():
    root = Path(__file__).resolve().parents[2]
    script = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'markdown', 'tkhtmlview'}:
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import core.ui
import core.ui.dialogs
import core.ui.recording_indicator
assert not hasattr(core.ui, 'toast')
assert not hasattr(core.ui, 'ToastMessageManager')
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
