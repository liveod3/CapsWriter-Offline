"""The local server follows the client language menu and validated file edits."""

import asyncio
import copy
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rich.console import Console

from config_templates import config_client_template
from core.client.llm.settings import save_ui_language
from core.i18n import get_language, set_language, tr
from core.i18n.preference import client_language_reloader


def make_follower(tmp_path, report):
    path = tmp_path / 'config_client.py'
    path.write_bytes(Path(config_client_template.__file__).read_bytes())
    save_ui_language(path, 'en')
    config = SimpleNamespace(**{
        key: copy.deepcopy(value) for key, value in vars(config_client_template.ClientConfig).items()
        if not key.startswith('_')
    })
    config.ui_language = 'en'
    module = SimpleNamespace(ClientConfig=config)
    follower = client_language_reloader(path, module, report)
    follower.poll()
    follower.poll()
    assert follower.last_error is None
    return follower, config


def server_for(follower):
    from core.server.app import CapsWriterServer
    server = CapsWriterServer.__new__(CapsWriterServer)
    server.client_language_reload = follower
    server.config_reload = SimpleNamespace(apply=lambda: ())
    server.process_manager = SimpleNamespace(publish_ui_language=Mock())
    return server


def test_menu_saved_preference_changes_server_console_tray_and_workers(tmp_path, monkeypatch):
    from core.server import app as module
    output = io.StringIO()
    monkeypatch.setattr(module, 'console', Console(file=output, width=180))
    refresh = Mock()
    monkeypatch.setattr('core.ui.tray.refresh_language', refresh)
    monkeypatch.setattr(module.Config, 'ui_language', 'en', raising=False)
    follower, client_config = make_follower(tmp_path, Mock())
    server = server_for(follower)
    set_language('en')
    for language in ['zh-CN', 'en']:
        save_ui_language(follower.path, language)
        follower.poll()
        assert follower.pending is None  # An incomplete edit must not be published.
        follower.poll()
        server.apply_config_reload()
        assert get_language() == language
        assert tr('language.server_following') in output.getvalue()
        server.process_manager.publish_ui_language.assert_called_with(language)
    assert refresh.call_count == 2
    assert client_config.ui_language == 'en'  # Detached follower never mutates client state.
    assert module.Config.ui_language == 'en'  # No server configuration writes.


@pytest.mark.parametrize('candidate', [
    "ui_language = 'invalid'",
    "ui_language = ['en']",
    "ui_language = 'zh-CN'\n    save_audio = 'invalid'",
    "ui_language = 'zh-CN'\n    ui_language = 'en'",
    'ui_language = ',
])
def test_invalid_client_edit_preserves_server_language(tmp_path, candidate):
    messages = []
    follower, config = make_follower(tmp_path, messages.append)
    original = follower.path.read_text(encoding='utf-8')
    follower.path.write_text(original.replace("ui_language = 'en'", candidate), encoding='utf-8')
    follower.poll()
    follower.poll()
    assert follower.apply() == ()
    assert follower.target.ui_language == config.ui_language == 'en'
    assert messages[-1].message_id == 'language.client_rejected'
    # A valid save recovers without restarting the follower.
    follower.path.write_text(original.replace("ui_language = 'en'", "ui_language = 'zh-CN'"), encoding='utf-8')
    follower.poll()
    follower.poll()
    assert follower.apply() == ('ui_language',)
    assert follower.target.ui_language == 'zh-CN'


def test_unrelated_client_changes_are_not_applied_or_reported(tmp_path):
    messages = []
    follower, config = make_follower(tmp_path, messages.append)
    original = follower.path.read_text(encoding='utf-8')
    follower.path.write_text(original.replace('save_audio = False', 'save_audio = True'), encoding='utf-8')
    follower.poll()
    follower.poll()
    assert follower.apply() == ()
    assert follower.target.save_audio is config.save_audio is False
    assert not messages


@pytest.mark.parametrize('language', ['en', 'zh-CN', 'auto'])
def test_server_startup_uses_client_preference_before_creating_workers(language, monkeypatch):
    import config_client
    from core.server import app as module
    monkeypatch.setattr(config_client.ClientConfig, 'ui_language', language, raising=False)
    monkeypatch.setattr(module.Config, 'ui_language', 'en' if language == 'zh-CN' else 'zh-CN')
    monkeypatch.setattr('core.i18n.system_language', lambda: 'zh-CN')
    monkeypatch.setattr(module, 'ServerState', Mock())
    seen = []
    monkeypatch.setattr(module, 'ProcessManager', lambda _app: seen.append(get_language()))
    monkeypatch.setattr(module, 'SocketManager', Mock())
    monkeypatch.setattr(module, 'TrayManager', Mock())
    server = module.CapsWriterServer()
    try:
        expected = 'zh-CN' if language == 'auto' else language
        assert get_language() == expected
        assert seen == [expected]
    finally:
        server.loop.close()
        asyncio.set_event_loop(None)


def test_legacy_client_without_language_uses_server_fallback(monkeypatch):
    import config_client
    from core.server import app as module
    monkeypatch.delattr(config_client.ClientConfig, 'ui_language')
    monkeypatch.setattr(module.Config, 'ui_language', 'zh-CN')
    monkeypatch.setattr(module, 'ServerState', Mock())
    monkeypatch.setattr(module, 'ProcessManager', Mock())
    monkeypatch.setattr(module, 'SocketManager', Mock())
    monkeypatch.setattr(module, 'TrayManager', Mock())
    server = module.CapsWriterServer()
    try:
        assert get_language() == 'zh-CN'
    finally:
        server.loop.close()
        asyncio.set_event_loop(None)


def test_language_watcher_is_closed_with_server(monkeypatch):
    from core.server import app as module
    server = module.CapsWriterServer.__new__(module.CapsWriterServer)
    server.is_alive = False
    server.loop = asyncio.new_event_loop()
    server.process_manager = SimpleNamespace(start=Mock())
    server.socket_manager = SimpleNamespace(prepare=Mock(), start=AsyncMock())
    server.tray_manager = SimpleNamespace(start=Mock())
    server._cleanup = Mock()
    server._print_banner = Mock()
    server.config_reload = SimpleNamespace(watch=AsyncMock(), close=AsyncMock())
    server.client_language_reload = SimpleNamespace(watch=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(module, 'register_signal', Mock())
    server.start()
    server.config_reload.close.assert_awaited_once()
    server.client_language_reload.close.assert_awaited_once()
    assert server.loop.is_closed()
