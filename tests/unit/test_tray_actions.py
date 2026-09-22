import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
from threading import RLock
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.client.manager.tray_manager import TrayManager
from core.client.app import CapsWriterClient
from core.client.state import ClientState
from core.client.llm.settings import llm_options, save_llm_options


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
        app.state = ClientState()
        app._stopping = False
        app._dictation_control_lock = RLock()
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


def test_independent_llm_switches_save_and_show_live_state(tmp_path, monkeypatch):
    config = SimpleNamespace(llm_enabled=False, llm_default_preset="correct_asr")
    monkeypatch.setattr("core.client.manager.tray_manager.Config", config)
    path = tmp_path / "config_client.py"
    path.write_text("class ClientConfig:\n    llm_enabled = False\n", encoding="utf-8")
    app = SimpleNamespace(
        base_dir=tmp_path,
        state=SimpleNamespace(dictation_paused=False, last_recognition_text=""),
        llm=SimpleNamespace(start=Mock(), process=AsyncMock(), directory=tmp_path / "LLM"),
    )
    manager = TrayManager(app)
    manager._schedule = lambda operation: asyncio.run(operation) or True
    monkeypatch.setattr("core.ui.show_status_hint", Mock())
    modes = next(a for a in manager.menu_actions() if a.to_item().text == "LLM actions").children
    assert [a.to_item().text for a in modes] == [
        "All LLM actions: Currently off", "Correction: Currently off", "Translation: Currently off"
    ]
    for index, correction, translation in (
        (0, True, True), (1, False, True), (0, True, True),
        (2, True, False), (1, False, False), (2, False, True),
        (1, True, True), (0, False, False),
    ):
        # Exercise pystray argument adaptation rather than calling callbacks directly.
        modes[index].to_item()(Mock(name="tray_icon"))
        assert config.llm_enabled == (correction or translation)
        assert config.llm_default_preset == "correct_asr"
        assert llm_options(config) == {"correct_asr": correction, "translate": translation}
        items = [a.to_item() for a in modes]
        assert [a.checked for a in items] == [correction and translation, correction, translation]
        assert not any(a.radio for a in items)
        assert items[1].text == (
            "Correction: Currently on" if correction else "Correction: Currently off"
        )
        assert items[2].text == (
            "Translation: Currently on" if translation else "Translation: Currently off"
        )
        if correction != translation:
            assert items[0].text == "All LLM actions: Currently partly on"
        saved = ast.parse(path.read_text(encoding="utf-8")).body[0]
        values = {n.targets[0].id: ast.literal_eval(n.value) for n in saved.body}
        assert values == {
            "llm_enabled": correction or translation,
            "llm_correction_enabled": correction,
            "llm_translation_enabled": translation,
        }
    app.llm.process.assert_not_called()


def test_failed_mode_save_keeps_runtime_settings(monkeypatch):
    config = SimpleNamespace(llm_enabled=True, llm_default_preset="correct_asr")
    monkeypatch.setattr("core.client.manager.tray_manager.Config", config)
    app = SimpleNamespace(base_dir=Path("."), llm=SimpleNamespace(start=Mock()))
    manager = TrayManager(app)
    manager._schedule = lambda operation: asyncio.run(operation) or True
    hint = Mock()
    monkeypatch.setattr("core.ui.show_status_hint", hint)
    with patch("core.client.manager.tray_manager.save_llm_options", side_effect=PermissionError):
        manager._toggle_llm_option("translate")
    assert config.llm_enabled and config.llm_default_preset == "correct_asr"
    app.llm.start.assert_not_called()
    assert "Could not save" in hint.call_args.args[0]
    assert not manager._mode_saving


def test_real_menu_dispatch_saves_settings_and_controls_next_request(tmp_path, monkeypatch):
    from pystray import Icon
    from core.client.llm.config import Catalog, Preset, Provider
    from core.client.llm.service import TextActionService

    # Use an in-memory provider and synthetic settings without credentials or network requests.
    provider = Provider("fixture", "ollama", "http://127.0.0.1:11434", "fixture")
    catalog = Catalog({"fixture": provider}, {
        "correct_asr": Preset("correct_asr", "Correction", "fixture", "Correct."),
        "translate": Preset("translate", "Translation", "fixture", "Translate.", ("翻译",)),
    })
    monkeypatch.setattr("core.client.llm.service.load_catalog", lambda _path: catalog)
    config = SimpleNamespace(
        llm_enabled=True, llm_correction_enabled=True, llm_translation_enabled=False,
        llm_default_preset="translate",
    )
    monkeypatch.setattr("core.client.manager.tray_manager.Config", config)
    path = tmp_path / "config_client.py"
    path.write_text(
        "class ClientConfig:\n" + "".join(
            f"    {key} = {value!r}\n" for key, value in vars(config).items()
        ), encoding="utf-8",
    )

    async def run():
        saved = asyncio.Event()
        monkeypatch.setattr("core.ui.show_status_hint", lambda *_args, **_kwargs: saved.set())
        transport = SimpleNamespace(complete=AsyncMock(return_value="synthetic result"))
        service = TextActionService(config, tmp_path, transport)
        service.start = Mock()
        app = SimpleNamespace(
            loop=asyncio.get_running_loop(), base_dir=tmp_path, state=SimpleNamespace(dictation_paused=False), llm=service,
        )
        manager = TrayManager(app)
        parent = next(a for a in manager.menu_actions() if a.to_item().text == "LLM actions")
        items = [action.to_item() for action in parent.children]
        icon = SimpleNamespace(update_menu=Mock())
        for index, correction, translation in (
            (0, True, True), (0, False, False), (1, True, False),
            (2, True, True), (1, False, True), (2, False, False),
        ):
            saved.clear()
            # Dispatch from the tray thread through pystray to the real event loop; retain _schedule.
            await asyncio.to_thread(Icon._handler(icon, items[index]))
            await asyncio.wait_for(saved.wait(), timeout=3)
            persisted = ast.parse(path.read_text(encoding="utf-8")).body[0]
            reloaded = SimpleNamespace(**{
                node.targets[0].id: ast.literal_eval(node.value) for node in persisted.body
            })
            assert vars(reloaded) == vars(config)
            assert reloaded.llm_enabled == (correction or translation)
            assert reloaded.llm_correction_enabled == correction
            assert reloaded.llm_translation_enabled == translation
            assert items[1].checked == correction and items[2].checked == translation
            expected = "translate" if translation else "correct_asr" if correction else None
            for active_service in (service, TextActionService(reloaded, tmp_path, transport)):
                for text in ("合成原文", "翻译：合成原文"):
                    before = transport.complete.await_count
                    result = await active_service.process(text)
                    assert result.preset_id == expected
                    assert transport.complete.await_count == before + int(expected is not None)
        assert icon.update_menu.call_count == 6

    asyncio.run(run())


def test_mode_save_preserves_user_code_comments_bom_and_newlines(tmp_path):
    path = tmp_path / "config_client.py"
    original = (
        "\ufeffraise AssertionError('must not execute')\r\n"
        "class ClientConfig:\r\n"
        "    shortcuts = [{'hotkey': 'f9'}]  # 用户快捷键\r\n"
        "    llm_enabled: bool = False  # 开关\r\n"
        "    llm_correction_enabled = False  # 润色\r\n"
        "    llm_translation_enabled = False  # 翻译\r\n"
        "    llm_default_preset = (\r\n        'correct_asr'\r\n    )  # 预设\r\n"
    ).encode("utf-8")
    path.write_bytes(original)
    save_llm_options(path, correction=True, translation=True)
    assert path.read_bytes() == original.replace(b"False", b"True")


def test_failed_atomic_save_keeps_original_file(tmp_path):
    path = tmp_path / "config_client.py"
    original = b"class ClientConfig:\n    llm_enabled = False\n"
    path.write_bytes(original)
    with patch("core.client.llm.settings.os.replace", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            save_llm_options(path, correction=True, translation=True)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("source", [
    "class ClientConfig:\n    llm_enabled = other = False\n",
    "class ClientConfig:\n    llm_enabled = False\n    llm_enabled = True\n",
    "class ClientConfig: [\n",
])
def test_ambiguous_or_invalid_config_is_not_overwritten(tmp_path, source):
    path = tmp_path / "config_client.py"
    path.write_text(source, encoding="utf-8")
    with pytest.raises((ValueError, SyntaxError)):
        save_llm_options(path, correction=True, translation=True)
    assert path.read_text(encoding="utf-8") == source
