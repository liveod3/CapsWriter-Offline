from types import SimpleNamespace
from threading import Event
from unittest.mock import Mock, patch

import numpy as np

from config_client import ClientConfig as Config
from core.client.audio.stream import AudioStreamManager
from core.client.shortcut.task import ShortcutTask


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

    with patch.object(Config, "input_device", None):
        assert manager.start(silent=True) is stream
        assert manager.start(silent=True) is stream

    query_devices.assert_called_once_with(device=None, kind="input")
    input_stream.assert_called_once()
    assert input_stream.call_args.kwargs["device"] is None
    stream.start.assert_called_once()
    thread_cls.return_value.start.assert_called_once()

    manager.stop()
    manager.stop()
    stream.close.assert_called_once()
    assert manager.state.stream is None
    assert manager._running is False


def test_audio_callback_marks_stream_ready_without_recording() -> None:
    manager = make_manager()
    ready_event = manager.get_ready_event()

    manager._audio_callback(
        np.zeros((2400, 1), dtype=np.float32),
        frames=2400,
        time_info=None,
        status=Mock(),
        ready_event=ready_event,
    )

    assert ready_event.is_set()


def test_stale_audio_ready_waiter_does_not_show_recording_ui() -> None:
    app = SimpleNamespace(stream=Mock())
    shortcut = SimpleNamespace(key="ctrl_r")
    task = ShortcutTask(app, shortcut)
    task.is_recording = True
    task._launch_generation = 2
    ready_event = Event()
    ready_event.set()
    app.stream.is_ready.return_value = True

    with (
        patch("core.client.shortcut.task.show_status_hint") as show_hint,
        patch("core.client.shortcut.task.show_recording_indicator") as show_indicator,
        patch("core.client.shortcut.task.set_recording_state") as set_recording,
    ):
        task._wait_for_audio_ready(ready_event, generation=1)

    show_hint.assert_not_called()
    show_indicator.assert_not_called()
    set_recording.assert_not_called()


def test_paused_monitor_reports_device_change_without_reopening() -> None:
    manager = make_manager()
    manager.state.dictation_paused = True
    manager._last_input_device = "Headset (WH-1000XM5)"

    with (
        patch.object(manager, "_commit_input_device") as commit_device,
        patch.object(manager, "reopen") as reopen,
    ):
        manager._handle_monitored_device("Microphone (Realtek(R) Audio)")

    commit_device.assert_called_once_with("Microphone (Realtek(R) Audio)")
    reopen.assert_not_called()


def test_active_monitor_reopens_before_committing_device_change() -> None:
    manager = make_manager()
    manager._running = True
    manager._last_input_device = "Headset (WH-1000XM5)"

    with (
        patch.object(manager, "_commit_input_device") as commit_device,
        patch.object(manager, "reopen") as reopen,
    ):
        manager._handle_monitored_device("Microphone (Realtek(R) Audio)")

    reopen.assert_called_once_with()
    commit_device.assert_not_called()


@patch("core.client.audio.stream.sd.query_devices")
@patch("core.client.audio.stream.sd._initialize")
@patch("core.client.audio.stream.sd._terminate")
def test_paused_monitor_refreshes_portaudio_before_query(terminate, initialize, query_devices) -> None:
    manager = make_manager()
    manager.state.dictation_paused = True
    query_devices.return_value = {"name": "Microphone (Realtek(R) Audio)", "max_input_channels": 2}

    assert manager._query_monitored_input_device() == query_devices.return_value

    terminate.assert_called_once_with()
    initialize.assert_called_once_with()
    query_devices.assert_called_once_with(device=None, kind="input")


@patch("core.client.audio.stream.sd.query_devices")
@patch("core.client.audio.stream.sd._initialize")
@patch("core.client.audio.stream.sd._terminate")
def test_active_monitor_does_not_refresh_portaudio(terminate, initialize, query_devices) -> None:
    manager = make_manager()
    manager._running = True
    query_devices.return_value = {"name": "Headset (WH-1000XM5)", "max_input_channels": 1}

    assert manager._query_monitored_input_device() == query_devices.return_value

    terminate.assert_not_called()
    initialize.assert_not_called()
    query_devices.assert_called_once_with(device=None, kind="input")


@patch("core.client.audio.stream.threading.Thread")
@patch("core.client.audio.stream.sd.InputStream")
@patch("core.client.audio.stream.sd.query_devices")
def test_start_uses_configured_input_device(query_devices, input_stream, thread_cls) -> None:
    manager = make_manager()
    stream = Mock()
    input_stream.return_value = stream
    query_devices.return_value = {"name": "Microphone (Realtek(R) Audio)", "max_input_channels": 2}

    configured_device = "Microphone (Realtek(R) Audio), Windows WASAPI"
    with patch.object(Config, "input_device", f"  {configured_device}  "):
        assert manager.start(silent=True) is stream

    query_devices.assert_called_once_with(device=configured_device, kind="input")
    assert input_stream.call_args.kwargs["device"] == configured_device
    thread_cls.return_value.start.assert_called_once()


@patch("core.client.audio.stream.threading.Thread")
@patch("core.client.audio.stream.sd.InputStream")
@patch("core.client.audio.stream.sd.query_devices")
def test_successful_start_commits_device_change(query_devices, input_stream, thread_cls) -> None:
    manager = make_manager()
    manager._last_input_device = "Headset (WH-1000XM5)"
    input_stream.return_value = Mock()
    query_devices.return_value = {"name": "Microphone (Realtek(R) Audio)", "max_input_channels": 2}

    with (
        patch.object(Config, "input_device", None),
        patch.object(manager, "_commit_input_device") as commit_device,
    ):
        assert manager.start(silent=True) is input_stream.return_value

    commit_device.assert_called_once_with("Microphone (Realtek(R) Audio)")


def test_stop_disables_monitor_when_stream_is_already_stopped() -> None:
    manager = make_manager()
    manager._monitor_running = True
    manager._monitor_thread = Mock()
    manager._monitor_thread.is_alive.return_value = False

    manager.stop()

    assert manager._monitor_running is False
    assert manager._monitor_thread is None


def test_reopen_serializes_stop_driver_refresh_and_start() -> None:
    manager = make_manager()
    replacement = Mock()

    with (
        patch.object(manager, "stop") as stop,
        patch.object(manager, "start", return_value=replacement) as start,
        patch("core.client.audio.stream.sd._terminate") as terminate,
        patch("core.client.audio.stream.sd._initialize") as initialize,
        patch.object(manager._shutdown, 'wait', return_value=False) as wait,
    ):
        assert manager.reopen() is replacement

    stop.assert_called_once_with(keep_monitor=True)
    terminate.assert_called_once_with()
    initialize.assert_called_once_with()
    wait.assert_called_once_with(0.1)
    start.assert_called_once_with()


@patch("core.client.audio.stream.threading.Thread")
def test_finished_callback_wakes_existing_monitor_without_spawning_threads(thread_cls) -> None:
    manager = make_manager()
    manager._running = True

    manager._on_stream_finished()

    thread_cls.assert_not_called()
    assert manager._monitor_wakeup.is_set()
    assert manager._recovery_requested is manager.get_ready_event()
