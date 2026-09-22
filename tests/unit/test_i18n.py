"""Locale resources, persistence, UI dispatch and content isolation regressions."""

import ast
import asyncio
import io
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest
from rich.console import Console

from core.i18n import CATALOGS, ENGLISH, Notice, get_language, lazy, set_language, tr


ROOT = Path(__file__).resolve().parents[2]


def test_catalogs_have_matching_placeholders_and_all_literal_ids():
    for language, catalog in CATALOGS.items():
        assert catalog.keys() == ENGLISH.keys(), language
        for key, english in ENGLISH.items():
            placeholders = lambda text: {
                (name, spec, conversion)
                for _, name, spec, conversion in Formatter().parse(text)
                if name is not None
            }
            assert catalog[key].strip(), (language, key)
            assert placeholders(catalog[key]) == placeholders(english), (language, key)
    for path in (ROOT / "core").rglob("*.py"):
        if {"export", "gguf"}.intersection(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"tr", "lazy", "Notice"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                assert node.args[0].value in ENGLISH, (path, node.lineno)


def test_fallback_lazy_resolution_and_notice_keep_diagnostics_english(monkeypatch):
    label = lazy("tray.pause")
    notice = Notice("config.applied", fields="ui_language")
    set_language("zh-CN")
    assert label(None) == "暂停听写"
    assert notice.localized() == "配置已生效：ui_language"
    assert str(notice) == "Configuration applied: ui_language"
    monkeypatch.delitem(CATALOGS["zh-CN"], "tray.pause")
    assert label(None) == "Pause dictation"
    assert tr("missing.id") == "missing.id"
    assert set_language("unsupported") == "en"
    assert set_language(None) == "en"
    monkeypatch.setattr("core.i18n.system_language", lambda: "zh-CN")
    assert set_language("auto") == "zh-CN"
    assert tr("result.transcription", locale="unknown") == "Transcription:"


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_cli_help_and_errors_preserve_commands_and_switches(
    language, monkeypatch, capsys, tmp_path
):
    from core.client.cli import Config, parse_client_command

    monkeypatch.setattr(Config, "ui_language", language, raising=False)
    with pytest.raises(SystemExit) as finished:
        parse_client_command(["transcribe", "--help"])
    assert finished.value.code == 0
    help_text = capsys.readouterr().out
    for switch in ("--format", "--recursive", "--no-recursive"):
        assert switch in help_text
    assert tr("cli.help") in help_text
    assert tr("cli.options") in help_text
    with pytest.raises(SystemExit) as failed:
        parse_client_command(["transcribe", str(tmp_path / "missing.wav")])
    assert failed.value.code == 2
    assert tr("cli.missing_path", value0=tmp_path / "missing.wav") in capsys.readouterr().err


def test_language_save_preserves_user_settings_and_handles_failure(tmp_path, monkeypatch):
    from core.client.llm.settings import save_ui_language

    path = tmp_path / "config_client.py"
    original = b'\xef\xbb\xbfclass ClientConfig:\r\n    language = "japanese"  # ASR\r\n    custom = "keep"\r\n'
    path.write_bytes(original)
    save_ui_language(path, "zh-CN")
    saved = path.read_bytes()
    assert saved.startswith(original)
    assert b"    ui_language = 'zh-CN'\r\n" in saved
    save_ui_language(path, "en")
    assert b"    ui_language = 'en'\r\n" in path.read_bytes()
    stable = path.read_bytes()
    with pytest.raises(ValueError):
        save_ui_language(path, "invalid")
    monkeypatch.setattr("core.client.llm.settings.os.replace", Mock(side_effect=PermissionError))
    with pytest.raises(PermissionError):
        save_ui_language(path, "auto")
    assert path.read_bytes() == stable
    assert list(tmp_path.iterdir()) == [path]


def test_real_language_menu_dispatch_saves_without_changing_inflight_language(
    tmp_path, monkeypatch
):
    from core.client.manager.tray_manager import TrayManager
    from pystray import Icon

    path = tmp_path / "config_client.py"
    path.write_text(
        "class ClientConfig:\n    ui_language = 'en'\n    language = 'japanese'\n", encoding="utf-8"
    )
    config = SimpleNamespace(ui_language="en")
    monkeypatch.setattr("core.client.manager.tray_manager.Config", config)

    async def run():
        saved = asyncio.Event()
        monkeypatch.setattr("core.ui.show_status_hint", lambda *_a, **_kw: saved.set())
        app = SimpleNamespace(
            base_dir=tmp_path,
            loop=asyncio.get_running_loop(),
            llm=SimpleNamespace(directory=tmp_path),
            state=SimpleNamespace(dictation_paused=False),
        )
        manager = TrayManager(app)
        settings = next(a for a in manager.menu_actions() if a.to_item().text == "Settings")
        language_menu = settings.children[0]
        items = [action.to_item() for action in language_menu.children]
        icon = SimpleNamespace(update_menu=Mock())
        for index, preference in ((2, "zh-CN"), (0, "auto"), (1, "en")):
            saved.clear()
            await asyncio.to_thread(Icon._handler(icon, items[index]))
            await asyncio.wait_for(saved.wait(), 2)
            assert f"ui_language = '{preference}'" in path.read_text(encoding="utf-8")
            assert config.ui_language == "en"
            assert get_language() == "en"
        config.ui_language = "zh-CN"
        set_language("zh-CN")
        assert settings.to_item().text == "设置"
        assert settings.tooltip(None).startswith("编辑")
        assert items[2].checked and not items[1].checked
        assert language_menu.to_item().text == "界面语言"

    asyncio.run(run())


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_llm_feedback_preserves_content_and_english_diagnostics(
    language, monkeypatch, tmp_path, caplog
):
    from core.client.llm.config import Catalog, Preset, Provider
    from core.client.llm.errors import api_error, describe_failure, localized_failure
    from core.client.llm.service import TextActionService

    set_language(language)
    error = api_error(429, {"error": {"code": "QUOTA_EXCEEDED", "message": "PRIVATE"}}, "12")
    assert error.category == "http_error"
    assert error.user_message == tr("llm.reason.quota_exceeded") + tr(
        "llm.retry_after", seconds="12"
    )
    assert describe_failure(error)[1].startswith("API quota exceeded.")
    assert localized_failure(RuntimeError("PRIVATE")) == tr("llm.unexpected_error")
    provider = Provider("fixture", "ollama", "http://localhost:11434", "fixture")
    preset = Preset("correct_asr", "User-defined name", "fixture", "CUSTOM PROMPT")
    monkeypatch.setattr(
        "core.client.llm.service.load_catalog",
        lambda _: Catalog({"fixture": provider}, {"correct_asr": preset}),
    )
    transport = SimpleNamespace(complete=AsyncMock(side_effect=error))
    config = SimpleNamespace(llm_enabled=True, llm_default_preset="correct_asr")
    text = "Synthetic English 与中文 {braces}"
    result = asyncio.run(TextActionService(config, tmp_path, transport).process(text))
    import json
    sent_messages = transport.complete.call_args.args[1]
    assert sent_messages[0]['content'] == 'CUSTOM PROMPT'
    assert json.loads(sent_messages[1]['content']) == {'transcript': text}
    assert result.text == text and result.input_text == text
    assert result.error_message == error.user_message
    assert (
        "CUSTOM PROMPT" not in caplog.text
        and text not in caplog.text
        and "PRIVATE" not in caplog.text
    )


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_file_feedback_does_not_translate_or_parse_user_filename(language):
    from core.client.transcribe.feedback import print_file_failure

    set_language(language)
    stream = io.StringIO()
    from rich.theme import Theme

    console = Console(
        file=stream,
        width=200,
        theme=Theme({f"ui.{key}": "" for key in ("error", "label", "value", "muted")}),
    )
    name = "[red]Synthetic 中文[/red].wav"
    print_file_failure(console, Path(name), "unknown", has_next=False)
    assert Path(name).name in stream.getvalue()
    assert tr("file.failure.unexpected.reason") in stream.getvalue()


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("width", [800, 1920])
def test_long_status_wraps_within_monitor_workarea(language, width, monkeypatch):
    from core.ui import recording_indicator as module

    set_language(language)
    root, window = Mock(), Mock()
    window.winfo_reqwidth.return_value = 700
    window.winfo_reqheight.return_value = 80
    monkeypatch.setattr(module.tk, "Toplevel", Mock(return_value=window))
    monkeypatch.setattr(module.tk, "Frame", Mock())
    label = Mock()
    monkeypatch.setattr(module.tk, "Label", label)
    monkeypatch.setattr(module, "_get_active_monitor_workarea", lambda: (0, 0, width, 1080))
    indicator = module._RecordingIndicator(root)
    text = tr("llm.missing_local_key") + tr("llm.original_retained")
    indicator._show_hint_impl(text, 2000, "#ffffff")
    kwargs = label.call_args.kwargs
    assert kwargs["text"] == text
    assert 0 < kwargs["wraplength"] < width - 50
    assert kwargs["justify"] == "left"
    root.after.assert_called_once()


def test_language_and_llm_saves_cannot_overwrite_each_other(tmp_path, monkeypatch):
    from core.client.manager.tray_manager import TrayManager

    async def run():
        writes = []
        queued = []
        manager = TrayManager(SimpleNamespace(base_dir=tmp_path))
        manager._schedule = queued.append
        started, release = asyncio.Event(), asyncio.Event()

        async def save_in_thread(function, *args, **kwargs):
            writes.append(function.__name__)
            started.set()
            await release.wait()

        monkeypatch.setattr('core.client.manager.tray_manager.asyncio.to_thread', save_in_thread)
        monkeypatch.setattr('core.ui.show_status_hint', Mock())
        manager._set_language('zh-CN')
        language_save = asyncio.create_task(queued.pop())
        await started.wait()
        manager._toggle_llm_option()
        await queued.pop()
        assert writes == ['save_ui_language']
        release.set()
        await language_save
        assert not manager._mode_saving
    asyncio.run(run())
