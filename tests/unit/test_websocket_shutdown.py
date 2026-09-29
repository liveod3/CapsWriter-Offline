import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from core.client.app import CapsWriterClient
from core.client.operations import ClientOperations
from core.client.connection.websocket_manager import CommunicationError, WebSocketManager
from core.client.output.result_processor import ResultProcessor
from core.client.state import ClientState


class FakeState(ClientState):
    @property
    def is_connected(self):
        return self.websocket is not None


def make_manager():
    app = SimpleNamespace(state=FakeState(), loop=None, progress=Mock())
    return WebSocketManager(app)


def closed_connection(code=1000):
    exception = ConnectionClosedOK if code == 1000 else ConnectionClosedError
    return exception(Close(code, ""), Close(code, ""), True)


def test_shutdown_while_receiving_exits_without_traceback_or_reconnect():
    async def run():
        manager = make_manager()
        manager.app.ws = manager
        manager.app.loop = asyncio.get_running_loop()
        manager.state.task_contexts["pending"] = ("synthetic reference", 42)
        processor = ResultProcessor(manager.app)
        receiving = asyncio.Event()
        closing = asyncio.Event()

        async def recv():
            receiving.set()
            await closing.wait()
            raise closed_connection()

        websocket = SimpleNamespace(recv=recv, close=AsyncMock(side_effect=closing.set))
        manager.state.websocket = websocket
        with (
            patch(
                "core.client.connection.websocket_manager.websockets.connect",
                new_callable=AsyncMock,
            ) as connect,
            patch("core.client.output.result_processor.console.print") as output,
        ):
            operation = asyncio.create_task(processor.start())
            await receiving.wait()
            processor.request_exit()
            manager.close_sync()
            manager.state.websocket = None  # Simulate State.reset() during app.stop().
            await asyncio.wait_for(operation, 1)
            connect.assert_not_awaited()
            output.assert_not_called()
        websocket.close.assert_awaited_once()
        assert not manager.state.task_contexts

    asyncio.run(run())


@pytest.mark.parametrize("code", [1000, 1011])
def test_remote_close_still_reports_failure_when_not_exiting(code):
    async def run():
        manager = make_manager()
        manager.state.websocket = SimpleNamespace(
            recv=AsyncMock(side_effect=closed_connection(code))
        )
        with pytest.raises(CommunicationError):
            await manager.receive()
        assert manager.state.websocket is None

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["remote_close", "invalid_json"])
def test_processor_reconnects_after_receive_failure(failure):
    async def run():
        manager = make_manager()
        manager.app.ws = manager
        manager.app.loop = asyncio.get_running_loop()
        processor = ResultProcessor(manager.app)
        first = SimpleNamespace(close=AsyncMock(), recv=AsyncMock())
        if failure == "remote_close":
            first.recv.side_effect = closed_connection(1011)
        else:
            first.recv.return_value = "invalid JSON"
        manager.state.websocket = first

        async def end_after_reconnect():
            processor.request_exit()
            await asyncio.sleep(0)
            raise closed_connection()

        second = SimpleNamespace(close=AsyncMock(), recv=AsyncMock(side_effect=end_after_reconnect))
        with (
            patch(
                "core.client.connection.websocket_manager.websockets.connect",
                new=AsyncMock(return_value=second),
            ) as connect,
            patch("core.client.output.result_processor.console.print"),
        ):
            await asyncio.wait_for(processor.start(), 1)
            connect.assert_awaited_once()
        second.recv.assert_awaited_once()
        if failure == "invalid_json":
            first.close.assert_awaited_once()

    asyncio.run(run())


def make_shutdown_app(calls):
    app = CapsWriterClient.__new__(CapsWriterClient)
    app._stopping = False
    app._shutdown_lock = threading.Lock()
    app._idle_stop = threading.Event()
    app._runner_task = None
    app.progress = Mock()
    app._active_runner = SimpleNamespace(
        processor=SimpleNamespace(request_exit=lambda: calls.append('processor')))
    app.stop_idle_suspend_monitor = Mock()
    for name in ('udp', 'shortcut', 'tray', 'llm'):
        setattr(app, name, SimpleNamespace(stop=Mock()))
    app.stream = SimpleNamespace(request_shutdown=Mock(), close=Mock())
    app.caret_context = SimpleNamespace(close=Mock())
    app.ws = SimpleNamespace(begin_shutdown=Mock(),
                             close=AsyncMock(side_effect=lambda: calls.append('websocket')))
    app.state = SimpleNamespace(reset=Mock(), recording_tasks=set())
    app.loop = asyncio.get_running_loop()
    return app


def test_client_stop_requests_processor_exit_before_closing_connection():
    async def run():
        calls = []
        app = make_shutdown_app(calls)

        await asyncio.to_thread(app.stop)
        await asyncio.wrap_future(app._shutdown_future)
        app.stop()
        assert calls == ['processor', 'websocket']
        app.stream.request_shutdown.assert_called_once()
        app.stream.close.assert_called_once()
        assert app.loop.is_running()

    asyncio.run(run())


def test_shutdown_waits_for_actual_recording_cleanup_after_future_cancellation():
    async def run():
        app = make_shutdown_app([])
        started, cleaning, allow_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cancelled_again = asyncio.Event()

        async def recorder():
            task = asyncio.current_task()
            app.state.recording_tasks.add(task)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                cleanup = asyncio.create_task(allow_cleanup.wait())
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled_again.set()
                app.state.recording_tasks.remove(task)

        future = asyncio.run_coroutine_threadsafe(recorder(), app.loop)
        await started.wait()
        app.shortcut.stop.side_effect = future.cancel
        # One failed component must not skip recording and device cleanup.
        app.udp.stop.side_effect = RuntimeError('synthetic')
        app.stop()
        await asyncio.wait_for(cleaning.wait(), 1)
        await asyncio.wait_for(cancelled_again.wait(), 1)
        assert future.cancelled()
        assert not app._shutdown_future.done()
        app.stream.close.assert_not_called()
        app.state.reset.assert_not_called()
        allow_cleanup.set()
        await asyncio.wait_for(asyncio.wrap_future(app._shutdown_future), 2)
        app.stream.close.assert_called_once()
        app.state.reset.assert_called_once()
        assert future.cancelled()

    asyncio.run(run())


@pytest.mark.parametrize('desktop_mode', [False, True])
@pytest.mark.parametrize('paused', [False, True])
def test_microphone_startup_respects_pause(monkeypatch, desktop_mode, paused):
    from core.client.manager.mic_runner import MicRunner
    monkeypatch.setattr('core.client.manager.mic_runner.TipsDisplay.show_mic_tips', Mock())
    monkeypatch.setattr('core.client.manager.mic_runner.Config.udp_control', False)
    publish = Mock()
    monkeypatch.setattr('core.client.manager.mic_runner.set_dictation_paused', publish)
    app = SimpleNamespace(state=ClientState(dictation_paused=paused, dictation_manually_paused=paused),
                          desktop_mode=desktop_mode, _stopping=False,
                          stream=SimpleNamespace(start=Mock()), tray=SimpleNamespace(start=Mock()),
                          shortcut=SimpleNamespace(start=Mock()), udp=SimpleNamespace(start=Mock()),
                          llm=SimpleNamespace(start=Mock()), start_idle_suspend_monitor=Mock())
    publish.side_effect = lambda _: app.tray.start.assert_called_once()
    asyncio.run(MicRunner(app).start_resources())
    assert app.stream.start.call_count == (0 if paused else 1)
    publish.assert_called_once_with(paused)
    app.shortcut.start.assert_called_once()
    assert not app.state.recording
    assert app.state.recording_owner is None and app.state.capture is None
    assert app.state.recording_tasks == set() and app.state.recorder_by_id == {}


@pytest.fixture
def isolated_client(monkeypatch):
    import core.client.app as module

    for name in ('TextActionService', 'CaretContextCapture', 'TextOutput', 'DiaryWriter',
                 'WebSocketManager', 'TrayManager', 'AudioStreamManager', 'ShortcutManager',
                 'UDPController', 'empty_current_working_set'):
        monkeypatch.setattr(module, name, Mock())
    monkeypatch.setattr(module.os, 'chdir', Mock())
    monkeypatch.setattr('core.client.operations.ClientOperations', Mock())
    monkeypatch.setattr('core.config_reload.ConfigReloader', Mock())
    monkeypatch.setattr('core.settings_gui.bridge.SettingsProcess', Mock())
    monkeypatch.setattr(module, 'set_dictation_paused', Mock())
    clients = []

    def create(mode):
        from core.client.cli import ClientCommand
        client = CapsWriterClient(ClientCommand(mode))
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.loop.close()
    asyncio.set_event_loop(None)


@pytest.mark.parametrize('mode', ['mic', 'transcribe', 'rebuild-srt'])
def test_client_initial_pause_applies_only_to_dictation(isolated_client, mode):
    from core.client.cli import ClientMode

    app = isolated_client(ClientMode(mode))
    assert app.state.dictation_paused == (mode == 'mic')
    assert not app.state.dictation_manually_paused
    assert not app.state.recording
    app.stream.start.assert_not_called()


@pytest.mark.parametrize('succeeds', [False, True])
def test_initial_standby_requires_successful_resume(isolated_client, succeeds):
    from core.client.cli import ClientMode

    app = isolated_client(ClientMode.MIC)
    app.stream.start.return_value = object() if succeeds else None
    assert app.resume_dictation(show_hint=False) == succeeds
    assert app.state.dictation_paused == (not succeeds)
    assert not app.state.dictation_manually_paused
    assert not app.state.recording
    app.stream.start.assert_called_once_with(silent=True, force=True)
    if succeeds:
        assert app.resume_dictation(show_hint=False)
        app.stream.start.assert_called_once()
    else:
        app.stream.start.return_value = object()
        assert app.resume_dictation(show_hint=False)
        assert not app.state.dictation_paused
        assert not app.state.dictation_manually_paused


@pytest.mark.parametrize('first_open_fails', [False, True])
def test_first_shortcut_wakes_startup_standby_and_records(isolated_client, monkeypatch, first_open_fails):
    from core.client.cli import ClientMode
    from core.client.shortcut.task import ShortcutTask

    app = isolated_client(ClientMode.MIC)
    app.progress = Mock()
    for name in ('show_status_hint', 'hide_status_hint', 'show_recording_indicator',
                 'hide_recording_indicator', 'set_recording_state', 'Status', 'Thread'):
        monkeypatch.setattr(f'core.client.shortcut.task.{name}', Mock())
    monkeypatch.setattr('core.client.caret_context.foreground_window', lambda: 0)
    app.stream.is_ready.return_value = False
    recorded = []

    class Recorder:
        task_id = 'startup-test'

        def __init__(self, app):
            pass

        async def record_and_send(self, capture):
            assert not app.state.dictation_paused
            assert app.stream.start.called
            recorded.append(capture)

    async def run():
        runtime = await ClientOperations.read_status(SimpleNamespace(app=app))
        assert runtime['paused'] and not runtime['manually_paused']
        task = ShortcutTask(app, SimpleNamespace(key='ctrl_r'), recorder_class=Recorder)
        app.stream.start.return_value = None if first_open_fails else object()
        assert task.launch()
        if first_open_fails:
            with pytest.raises(RuntimeError, match='MicrophoneResumeFailed'):
                await asyncio.wrap_future(task.task)
            assert app.state.dictation_paused and not app.state.dictation_manually_paused
            assert not recorded
            app.stream.start.return_value = object()
            assert task.launch()
        await asyncio.wrap_future(task.task)
        assert len(recorded) == 1
        assert not app.state.recording and app.state.recording_owner is None
        assert not app.state.dictation_paused
        assert app.pause_dictation(show_hint=False)
        assert app.state.dictation_manually_paused
        calls = app.stream.start.call_count
        assert not task.launch()
        assert app.stream.start.call_count == calls

    app.loop.run_until_complete(run())


def test_shutdown_during_microphone_start_does_not_restart_listeners(monkeypatch):
    from core.client.manager.mic_runner import MicRunner

    monkeypatch.setattr('core.client.manager.mic_runner.TipsDisplay.show_mic_tips', Mock())

    async def run():
        app = make_shutdown_app([])
        app.state.dictation_paused = False
        app.state.dictation_manually_paused = False
        app.tray.start = Mock()
        app.shortcut.start = Mock()
        app.udp.start = Mock()
        app.llm.start = Mock()
        app.start_idle_suspend_monitor = Mock()
        entered, release = threading.Event(), threading.Event()

        def open_stream():
            entered.set()
            assert release.wait(2)

        app.stream.start = open_stream
        runner = MicRunner(app)
        operation = asyncio.create_task(runner.run())
        assert await asyncio.to_thread(entered.wait, 1)
        app._stopping = True
        release.set()
        await asyncio.wait_for(operation, 2)
        app.shortcut.start.assert_not_called()
        app.udp.start.assert_not_called()
        app.llm.start.assert_not_called()
        app.start_idle_suspend_monitor.assert_not_called()
        assert runner.processor is None

    asyncio.run(run())


def test_shutdown_waits_for_file_runner_cleanup_before_reset():
    async def run():
        app = make_shutdown_app([])
        running, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def runner():
            running.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        app._runner_task = asyncio.create_task(runner())
        await running.wait()
        app.stop()
        await asyncio.wait_for(cleaning.wait(), 1)
        assert not app._shutdown_future.done()
        app.state.reset.assert_not_called()
        release.set()
        await asyncio.wait_for(asyncio.wrap_future(app._shutdown_future), 1)
        app.state.reset.assert_called_once()
        assert app._runner_task.cancelled()

    asyncio.run(run())


def test_close_sync_without_connection_still_disables_reconnect():
    async def run():
        manager = make_manager()
        manager.close_sync()

        with patch(
            "core.client.connection.websocket_manager.websockets.connect",
            new_callable=AsyncMock,
        ) as connect:
            assert await manager.connect() is False
            connect.assert_not_awaited()

    asyncio.run(run())


def test_regular_async_close_allows_later_reconnect():
    async def run():
        manager = make_manager()
        first_websocket = SimpleNamespace(close=AsyncMock())
        second_websocket = SimpleNamespace(close=AsyncMock())
        manager.state.websocket = first_websocket

        await manager.close()

        with patch(
            "core.client.connection.websocket_manager.websockets.connect",
            new=AsyncMock(return_value=second_websocket),
        ) as connect:
            assert await manager.connect() is True
            connect.assert_awaited_once()

        first_websocket.close.assert_awaited_once()
        assert manager.state.websocket is second_websocket

    asyncio.run(run())


def test_connection_success_can_be_silent_for_file_mode():
    async def run():
        manager = make_manager()
        websocket = SimpleNamespace(close=AsyncMock())

        with (
            patch(
                "core.client.connection.websocket_manager.websockets.connect",
                new=AsyncMock(return_value=websocket),
            ),
            patch("core.client.connection.websocket_manager.console.print") as output,
        ):
            assert await manager.connect(announce=False) is True
            output.assert_not_called()

    asyncio.run(run())


def test_connection_completed_during_shutdown_is_closed_without_being_published():
    async def run():
        manager = make_manager()
        connect_started = asyncio.Event()
        allow_connect = asyncio.Event()
        websocket = SimpleNamespace(close=AsyncMock())

        async def delayed_connect(**kwargs):
            connect_started.set()
            await allow_connect.wait()
            return websocket

        with patch(
            "core.client.connection.websocket_manager.websockets.connect",
            side_effect=delayed_connect,
        ):
            connect_task = asyncio.create_task(manager.connect())
            await connect_started.wait()
            manager.begin_shutdown()
            allow_connect.set()

            assert await connect_task is False

        websocket.close.assert_awaited_once()
        assert manager.state.websocket is None

    asyncio.run(run())
