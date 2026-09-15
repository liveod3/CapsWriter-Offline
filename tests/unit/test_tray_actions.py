import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.client.manager.tray_manager import TrayManager
from core.client.app import CapsWriterClient
from core.client.llm.service import TextResult


def test_menu_removes_legacy_features_and_has_descriptions():
    state = SimpleNamespace(dictation_paused=False, last_output_text="", last_recognition_text="")
    app = SimpleNamespace(
        base_dir=Path("."), state=state, llm=SimpleNamespace(cancel=Mock(), directory=Path("LLM"))
    )
    actions = TrayManager(app).menu_actions()
    labels = [a.label(None) if callable(a.label) else a.label for a in actions]
    assert not any(label in labels for label in ("Context", "Hotwords", "Clear memory"))
    assert all(action.tooltip and action.icon for action in actions)
    assert labels[0] == "Pause dictation"
    state.dictation_paused = True
    assert actions[0].label(None) == "Resume dictation"


def test_manual_and_idle_pause_have_different_wakeup_policy():
    for manual in (True, False):
        app = CapsWriterClient.__new__(CapsWriterClient)
        app.state = SimpleNamespace(
            recording=False, dictation_paused=False, dictation_manually_paused=False
        )
        app.stream = SimpleNamespace(stop=Mock())
        with patch("core.client.app.set_dictation_paused"):
            assert app.pause_dictation(show_hint=False, manual=manual)
        assert app.state.dictation_manually_paused == manual
        app.stream.stop.assert_called_once_with(keep_monitor=True)


def test_settings_use_editor_not_python_association(tmp_path):
    path = tmp_path / "config_client.py"
    path.write_text("raise AssertionError('must not execute')", encoding="utf-8")
    manager = TrayManager(SimpleNamespace())
    with patch("core.client.manager.tray_manager.subprocess.Popen") as start:
        with patch("core.client.manager.tray_manager.os.startfile") as associated:
            manager._open(path)
    start.assert_called_once_with(["notepad.exe", str(path)])
    associated.assert_not_called()


def test_failed_tray_action_copies_original_and_shows_remedy(monkeypatch):
    detail = "API key missing. Fill api_key in providers.toml."
    app = SimpleNamespace(
        state=SimpleNamespace(last_recognition_text="original", set_output_text=Mock()),
        llm=SimpleNamespace(
            process=AsyncMock(
                return_value=TextResult(
                    "original",
                    "original",
                    error="MissingAPIKeyError",
                    error_message=detail,
                )
            )
        ),
    )
    manager = TrayManager(app)
    manager._copy_result = Mock()
    manager._schedule = lambda operation: asyncio.run(operation) or True
    hint = Mock()
    monkeypatch.setattr("core.ui.show_status_hint", hint)
    manager._text_action("translate")
    app.state.set_output_text.assert_called_once_with("original")
    manager._copy_result.assert_called_once()
    hint.assert_called_once_with(detail + " Original copied.", duration_ms=5000)
    assert not manager._action_running
