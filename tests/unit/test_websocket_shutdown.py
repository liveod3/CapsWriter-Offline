import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.client.app import CapsWriterClient
from core.client.connection.websocket_manager import WebSocketManager


class FakeState:
    def __init__(self):
        self.websocket = None

    @property
    def is_connected(self):
        return self.websocket is not None


def make_manager():
    app = SimpleNamespace(state=FakeState(), loop=None)
    return WebSocketManager(app)


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
