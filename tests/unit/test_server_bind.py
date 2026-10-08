"""Startup binding errors must be actionable and avoid unnecessary model loading."""

import asyncio
import errno
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from core.server.app import CapsWriterServer
from core.server.connection import server_manager as module


@pytest.mark.parametrize(('code', 'key'), [
    (10013, 'bind_permission_denied'),
    (10048, 'port_conflict_is_already_in_use_check_whether'),
    (errno.EACCES, 'bind_permission_denied'),
    (errno.EADDRINUSE, 'port_conflict_is_already_in_use_check_whether'),
    (errno.EADDRNOTAVAIL, 'bind_failed'),
])
def test_bind_error_classification_and_socket_release(monkeypatch, code, key):
    error = OSError(code, 'synthetic')
    if code >= 10000:
        error.winerror = code
    probe = MagicMock()
    probe.__enter__.return_value.bind.side_effect = error
    monkeypatch.setattr('socket.socket', Mock(return_value=probe))
    log = Mock()
    monkeypatch.setattr(module.logger, 'error', log)
    manager = module.SocketManager(SimpleNamespace())

    assert not manager._check_port()
    notice = log.call_args.args[0]
    assert notice.message_id == 'diagnostic.server_manager.' + key
    probe.__exit__.assert_called_once()


def test_failed_preflight_skips_models_tray_and_listener(monkeypatch):
    app = CapsWriterServer.__new__(CapsWriterServer)
    app.is_alive = False
    app.state = SimpleNamespace(queue_out=Mock())
    app.loop = asyncio.new_event_loop()
    app.process_manager = SimpleNamespace(start=Mock(), stop=Mock())
    app.tray_manager = SimpleNamespace(start=Mock(), stop=Mock())
    app.socket_manager = SimpleNamespace(
        prepare=Mock(), _check_port=Mock(return_value=False), start=AsyncMock(), stop=Mock(),
    )
    monkeypatch.setattr('core.server.app.register_signal', Mock())

    app.start()

    app.process_manager.start.assert_not_called()
    app.tray_manager.start.assert_not_called()
    app.socket_manager.start.assert_not_awaited()
    assert app.loop.is_closed()
    assert not app.is_alive
    app.process_manager.stop.assert_called_once()


@pytest.mark.parametrize('code', [10013, 10048])
def test_actual_listener_bind_failure_after_preflight_resets_state(monkeypatch, code):
    async def run():
        manager = module.SocketManager(SimpleNamespace(loop=asyncio.get_running_loop()))
        manager._prepared = True
        manager._check_port = Mock(return_value=True)
        manager._report_bind_error = Mock()
        error = OSError(code, 'synthetic')
        server = MagicMock()
        server.__aenter__.side_effect = error
        monkeypatch.setattr(module.websockets, 'serve', Mock(return_value=server))
        sender = AsyncMock()
        monkeypatch.setattr(module, 'ws_send', sender)

        await manager.start()

        manager._report_bind_error.assert_called_once_with(error)
        sender.assert_not_awaited()
        assert not manager._is_running
        assert manager._server is None

    asyncio.run(run())


@pytest.mark.parametrize('error', [OSError('synthetic sender failure'), asyncio.CancelledError()])
def test_running_listener_errors_propagate_and_reset_state(monkeypatch, error):
    async def run():
        manager = module.SocketManager(SimpleNamespace(loop=asyncio.get_running_loop()))
        manager._prepared = True
        manager._check_port = Mock(return_value=True)
        manager._report_bind_error = Mock()
        server = MagicMock()
        server.__aenter__.return_value = server
        monkeypatch.setattr(module.websockets, 'serve', Mock(return_value=server))
        monkeypatch.setattr(module, 'ws_send', AsyncMock(side_effect=error))

        with pytest.raises(type(error)):
            await manager.start()

        server.__aexit__.assert_awaited_once()
        manager._report_bind_error.assert_not_called()
        assert not manager._is_running
        assert manager._server is None

    asyncio.run(run())
