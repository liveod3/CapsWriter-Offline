"""Real control threads against a fake PortAudio backend; no hardware access."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.client.audio.stream import AudioStreamManager
from core.client.audio.portaudio_compat import refresh_devices
from core.client.state import ClientState


@pytest.fixture
def backend(monkeypatch):
    created = []
    replacement = threading.Event()

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']
            self.finished = kwargs['finished_callback']
            self.close = Mock()
            created.append(self)

        def start(self):
            if len(created) == 2:
                replacement.set()

    monkeypatch.setattr('core.client.audio.stream.sd.InputStream', Stream)
    monkeypatch.setattr('core.client.audio.stream.sd.query_devices',
                        Mock(return_value={'name': 'synthetic device', 'max_input_channels': 1}))
    refresh = Mock(return_value=True)
    monkeypatch.setattr('core.client.audio.stream.refresh_devices', refresh)
    manager = AudioStreamManager(SimpleNamespace(state=ClientState(), loop=Mock()))
    manager._commit_input_device = Mock()
    yield manager, created, replacement, refresh
    manager.close()


def test_repeated_finished_callbacks_share_one_recovery_owner(backend):
    manager, created, replacement, refresh = backend
    manager.start(silent=True)
    monitor = manager._monitor_thread
    old = created[0]
    with manager._stream_lock:
        for _ in range(100):
            old.finished()
    assert replacement.wait(2)
    assert manager._monitor_thread is monitor
    with manager._stream_lock:
        for _ in range(100):
            old.finished()
        assert manager._recovery_requested is None
        assert len(created) == 2
    old.close.assert_called_once()
    refresh.assert_called_once()


def test_pause_discards_already_queued_recovery(backend):
    manager, created, _, refresh = backend
    manager.start(silent=True)
    with manager._stream_lock:
        created[0].finished()
        manager.state.dictation_paused = True
        manager.stop(keep_monitor=True)
    manager.close()
    assert len(created) == 1
    refresh.assert_not_called()
    assert manager._monitor_thread is None


def test_shutdown_discards_queued_recovery_and_all_late_starts(backend):
    manager, created, _, refresh = backend
    manager.start(silent=True)
    with manager._stream_lock:
        created[0].finished()
        manager.request_shutdown()
    manager.close()
    created[0].finished()
    assert manager.start(force=True) is None
    assert manager.reopen() is None
    assert manager._monitor_thread is None
    assert len(created) == 1
    refresh.assert_not_called()


def test_stop_during_slow_open_cannot_publish_or_restart_stream(backend):
    manager, created, _, _ = backend
    entered, release = threading.Event(), threading.Event()
    from core.client.audio import stream as module
    original_start = module.sd.InputStream.start

    def slow_start(stream):
        entered.set()
        assert release.wait(2)
        original_start(stream)

    module.sd.InputStream.start = slow_start
    result = []
    opening = threading.Thread(target=lambda: result.append(manager.start(silent=True)))
    opening.start()
    assert entered.wait(2)
    manager.request_shutdown()
    release.set()
    opening.join(2)
    manager.close()
    assert not opening.is_alive()
    assert result == [None]
    assert manager.state.stream is None
    assert not manager._running
    created[0].close.assert_called_once()


def test_failed_start_closes_partial_stream(backend, monkeypatch):
    manager, created, _, _ = backend
    from core.client.audio import stream as module
    monkeypatch.setattr(module.sd.InputStream, 'start', Mock(side_effect=RuntimeError('synthetic')))
    assert manager.start(silent=True) is None
    created[0].close.assert_called_once()
    assert manager.state.stream is None
    assert not manager._running


def test_failed_close_prevents_private_reinitialization(backend):
    manager, created, _, refresh = backend
    manager.start(silent=True)
    created[0].close.side_effect = RuntimeError('synthetic close failure')
    with pytest.raises(RuntimeError):
        manager.reopen()
    refresh.assert_not_called()
    assert manager.state.stream is created[0]
    created[0].close.side_effect = None


def test_interruption_callback_cannot_cancel_a_newer_capture(backend):
    manager, _, _, _ = backend
    callbacks = []
    manager.app.loop.call_soon_threadsafe = callbacks.append
    manager.state.capture = object()
    manager.state.recording_owner = Mock()
    owner = manager.state.recording_owner
    manager._cancel_interrupted_capture()
    manager.state.capture = object()
    callbacks.pop()()
    owner.cancel.assert_not_called()


@pytest.mark.parametrize('version,allowed', [('0.5.5', True), ('0.6.0', False), ('', False)])
def test_private_refresh_has_explicit_version_boundary(version, allowed):
    backend = SimpleNamespace(__version__=version, _initialized=1,
                              _terminate=Mock(), _initialize=Mock())
    assert refresh_devices(backend) is allowed
    assert backend._initialize.call_count == int(allowed)


def test_private_refresh_failure_is_reported_without_unloading_dll():
    backend = SimpleNamespace(__version__='0.5.5', _initialized=1, _terminate=Mock(),
                              _initialize=Mock(side_effect=OSError('synthetic')))
    assert refresh_devices(backend) is False


def test_private_refresh_retry_preserves_initialization_count():
    backend = SimpleNamespace(__version__='0.5.5', _initialized=1)

    def terminate():
        backend._initialized -= 1

    def initialize():
        backend._initialized += 1

    backend._terminate = Mock(side_effect=terminate)
    backend._initialize = Mock(side_effect=OSError('synthetic'))
    assert refresh_devices(backend) is False
    assert backend._initialized == 0
    backend._initialize.side_effect = initialize
    assert refresh_devices(backend) is True
    assert backend._initialized == 1
    backend._terminate.assert_called_once()


@pytest.mark.parametrize('count', [None, -1, 2])
def test_private_refresh_rejects_unknown_or_shared_state(count):
    backend = SimpleNamespace(__version__='0.5.5', _initialized=count,
                              _terminate=Mock(), _initialize=Mock())
    assert refresh_devices(backend) is False
    backend._terminate.assert_not_called()
    backend._initialize.assert_not_called()


def test_recording_resume_does_not_block_event_loop_or_shortcut_thread(monkeypatch):
    from core.client.shortcut.task import ShortcutTask
    entered, release = threading.Event(), threading.Event()
    for name in ('show_status_hint', 'hide_status_hint', 'show_recording_indicator',
                 'hide_recording_indicator', 'set_recording_state', 'Status', 'Thread'):
        monkeypatch.setattr(f'core.client.shortcut.task.{name}', Mock())
    monkeypatch.setattr('core.client.caret_context.foreground_window', lambda: 0)

    async def run():
        state = ClientState(dictation_paused=True)

        def resume(**kwargs):
            entered.set()
            assert release.wait(2)
            state.dictation_paused = False
            return True

        class Recorder:
            task_id = 'synthetic'

            def __init__(self, app):
                pass

            async def record_and_send(self, capture):
                await asyncio.Event().wait()

        app = SimpleNamespace(state=state, loop=asyncio.get_running_loop(), _stopping=False,
                              mark_user_activity=Mock(), progress=Mock(), resume_dictation=resume,
                              stream=SimpleNamespace(get_ready_event=threading.Event,
                                                     is_ready=lambda _: False))
        shortcut = ShortcutTask(app, SimpleNamespace(key='ctrl_r'), recorder_class=Recorder)
        assert shortcut.launch()
        assert await asyncio.to_thread(entered.wait, 2)
        assert state.recording_tasks
        shortcut.cancel()
        await asyncio.sleep(0)
        assert state.recording_tasks  # Cancellation does not pretend open has finished.
        operations = list(state.recording_tasks)
        release.set()
        await asyncio.gather(*operations, return_exceptions=True)
        assert not state.recording_tasks
        assert not state.recording

    asyncio.run(run())
