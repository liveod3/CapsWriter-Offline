from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.client.audio.stream import AudioStreamManager


def make_manager() -> AudioStreamManager:
    state = SimpleNamespace(
        recording=False,
        dictation_paused=False,
        queue_in=None,
        stream=None,
    )
    app = SimpleNamespace(state=state, loop=None)
    return AudioStreamManager(app)


def test_manager_initializes_lifecycle_lock() -> None:
    manager = make_manager()

    assert manager._stream_lock is not None
    with manager._stream_lock:
        assert manager._running is False


@patch("core.client.audio.stream.threading.Thread")
@patch("core.client.audio.stream.sd.InputStream")
@patch("core.client.audio.stream.sd.query_devices")
def test_start_and_stop_are_idempotent(query_devices, input_stream, thread_cls) -> None:
    manager = make_manager()
    stream = Mock()
    input_stream.return_value = stream
    query_devices.return_value = {"name": "Mock microphone", "max_input_channels": 2}

    assert manager.start(silent=True) is stream
    assert manager.start(silent=True) is stream
    input_stream.assert_called_once()
    stream.start.assert_called_once()
    thread_cls.return_value.start.assert_called_once()

    manager.stop()
    manager.stop()
    stream.close.assert_called_once()
    assert manager.state.stream is None
    assert manager._running is False


def test_reopen_serializes_stop_driver_refresh_and_start() -> None:
    manager = make_manager()
    replacement = Mock()

    with (
        patch.object(manager, "stop") as stop,
        patch.object(manager, "start", return_value=replacement) as start,
        patch("core.client.audio.stream.sd._terminate") as terminate,
        patch("core.client.audio.stream.sd._initialize") as initialize,
        patch("core.client.audio.stream.time.sleep") as sleep,
    ):
        assert manager.reopen() is replacement

    stop.assert_called_once_with(keep_monitor=True)
    terminate.assert_called_once_with()
    initialize.assert_called_once_with()
    sleep.assert_called_once_with(0.1)
    start.assert_called_once_with()


@patch("core.client.audio.stream.threading.Thread")
def test_finished_callback_schedules_reopen_without_running_it_inline(thread_cls) -> None:
    manager = make_manager()
    manager._running = True

    manager._on_stream_finished()

    thread_cls.assert_called_once_with(
        target=manager.reopen,
        daemon=True,
        name="stream-reopen",
    )
    thread_cls.return_value.start.assert_called_once_with()
