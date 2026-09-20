import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from core.client.app import CapsWriterClient
from core.client.connection.websocket_manager import CommunicationError, WebSocketManager
from core.client.output.result_processor import ResultProcessor


class FakeState:
    def __init__(self):
        self.websocket = None
        self.task_contexts = {}

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
            manager.state.websocket = None  # 模拟 app.stop() 的 State.reset()
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


def test_shutdown_during_microphone_start_does_not_restart_listeners(monkeypatch):
    from core.client.manager.mic_runner import MicRunner

    monkeypatch.setattr('core.client.manager.mic_runner.TipsDisplay.show_mic_tips', Mock())

    async def run():
        app = make_shutdown_app([])
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
