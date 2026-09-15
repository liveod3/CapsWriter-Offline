import asyncio
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
    app = SimpleNamespace(state=FakeState(), loop=None)
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


def test_client_stop_requests_processor_exit_before_closing_connection():
    calls = []
    app = CapsWriterClient.__new__(CapsWriterClient)
    app._stopping = False
    app._active_runner = SimpleNamespace(
        processor=SimpleNamespace(request_exit=lambda: calls.append("processor"))
    )
    app.stop_idle_suspend_monitor = Mock()
    for name in ("udp", "shortcut", "stream", "tray", "llm"):
        setattr(app, name, SimpleNamespace(stop=Mock()))
    app.caret_context = SimpleNamespace(close=Mock())
    app.ws = SimpleNamespace(close_sync=lambda: calls.append("websocket"))
    app.state = SimpleNamespace(reset=Mock())
    app.loop = SimpleNamespace(stop=Mock())

    with patch("core.client.app.console.print"):
        CapsWriterClient.stop(app)

    assert calls == ["processor", "websocket"]
    app.loop.stop.assert_not_called()


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
