import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.client.llm.config import Catalog, Preset, Provider, load_catalog
from core.client.llm.service import TextActionService

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_LLM = ROOT / "LLM"


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path, monkeypatch):
    # 测试只使用公开模板，绝不读取开发机的 providers.toml 或真实凭据。
    base = tmp_path / "fixture"
    directory = base / "LLM"
    directory.mkdir(parents=True)
    (directory / "providers.toml").write_bytes(
        (PUBLIC_LLM / "providers.template.toml").read_bytes()
    )
    (directory / "presets.toml").write_bytes((PUBLIC_LLM / "presets.toml").read_bytes())
    monkeypatch.setitem(globals(), "ROOT", base)


def test_request_deadline_cancels_transport_and_preserves_input(monkeypatch):
    async def run():
        closed = asyncio.Event()

        async def complete(*args):
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        catalog = Catalog(
            {"p": Provider("p", "ollama", "http://localhost:11434", "model", timeout=0.01)},
            {"correct_asr": Preset("correct_asr", "Correct", "p", "Correct text.")},
        )
        monkeypatch.setattr("core.client.llm.service.load_catalog", lambda _: catalog)
        service = TextActionService(
            config(llm_enabled=True), ROOT, SimpleNamespace(complete=complete)
        )
        result = await service.process("原文")
        assert result.text == "原文" and result.error == "TimeoutError"
        assert closed.is_set() and not service._active

    asyncio.run(run())


def config(**values):
    return SimpleNamespace(llm_config_dir="LLM", **values)


def test_catalog_separates_connections_and_presets():
    catalog = load_catalog(ROOT / "LLM")
    assert set(catalog.presets) == {"correct_asr", "translate"}
    assert catalog.presets["correct_asr"].use_caret_context is False
    preset, text = catalog.select("翻译：今天有点冷", "correct_asr")
    assert preset.id == "translate"
    assert text == "今天有点冷"


@pytest.mark.parametrize("default", [[], ["correct_asr"], "missing", False])
def test_default_must_be_one_existing_id_or_none(default):
    with pytest.raises(ValueError):
        load_catalog(ROOT / "LLM").select("普通听写", default)


def test_none_default_does_not_disable_explicit_translation():
    catalog = load_catalog(ROOT / "LLM")
    assert catalog.select("普通听写", None) == (None, "普通听写")
    assert catalog.select("翻译：你好", None)[0].id == "translate"


def test_disabled_service_does_not_load_config_or_call_provider(tmp_path):
    transport = SimpleNamespace(complete=AsyncMock())
    service = TextActionService(config(llm_enabled=False), tmp_path, transport)
    result = asyncio.run(service.process("苦的"))
    assert result.text == "苦的"
    transport.complete.assert_not_called()


def test_empty_transcription_never_calls_provider(tmp_path):
    transport = SimpleNamespace(complete=AsyncMock())
    service = TextActionService(config(llm_enabled=True), tmp_path, transport)
    result = asyncio.run(service.process("  ", context="existing document"))
    assert result.text == "  " and not result.error
    transport.complete.assert_not_called()


def test_requests_are_stateless_and_context_is_opt_in():
    async def run():
        transport = SimpleNamespace(complete=AsyncMock(side_effect=["一", "二"]))
        service = TextActionService(config(llm_enabled=True), ROOT, transport)
        await service.process("first", context="private surrounding text")
        await service.process("second")
        first = transport.complete.call_args_list[0].args[1]
        second = transport.complete.call_args_list[1].args[1]
        assert len(first) == len(second) == 2
        assert json.loads(first[1]["content"]) == {"transcript": "first"}
        assert json.loads(second[1]["content"]) == {"transcript": "second"}

    asyncio.run(run())


def test_explicit_preset_makes_only_one_call():
    transport = SimpleNamespace(complete=AsyncMock(return_value="hello"))
    service = TextActionService(config(llm_enabled=True), ROOT, transport)
    result = asyncio.run(service.process("翻译：你好"))
    assert result.preset_id == "translate"
    assert result.input_text == "你好"
    transport.complete.assert_awaited_once()


def test_failure_keeps_original_without_response_body(monkeypatch):
    warning = Mock()
    monkeypatch.setattr("core.client.logger.warning", warning)
    transport = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError("secret response")))
    service = TextActionService(config(llm_enabled=True), ROOT, transport)
    result = asyncio.run(service.process("苦的"))
    assert result.text == "苦的"
    assert result.error == "RuntimeError"
    assert result.error_message == ""
    assert "secret response" not in str(warning.call_args)
    assert not result.processed


@pytest.mark.parametrize("preset_id", ["correct_asr", "translate"])
@pytest.mark.parametrize("from_environment", [False, True])
def test_missing_key_explains_remedy_without_network(monkeypatch, preset_id, from_environment):
    network = Mock(side_effect=AssertionError("network must not start"))
    monkeypatch.setattr("httpx.AsyncClient", network)
    warning = Mock()
    monkeypatch.setattr("core.client.logger.warning", warning)
    if from_environment:
        monkeypatch.delenv("TEST_CAPS_MISSING_KEY", raising=False)
        path = ROOT / "LLM/providers.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'api_key = ""', 'api_key_env = "TEST_CAPS_MISSING_KEY"'
            ),
            encoding="utf-8",
        )
    service = TextActionService(config(llm_enabled=True), ROOT)
    result = asyncio.run(service.process("original", preset_id=preset_id))
    assert result.text == "original" and not result.processed
    assert result.error == "MissingAPIKeyError"
    assert "API key" in result.error_message
    assert ("api_key_env" if from_environment else "providers.toml") in result.error_message
    assert result.error_message in str(warning.call_args)
    network.assert_not_called()


def test_cancel_closes_inflight_coroutine_and_keeps_original():
    async def run():
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def complete(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        service = TextActionService(
            config(llm_enabled=True), ROOT, SimpleNamespace(complete=complete)
        )
        operation = asyncio.create_task(service.process("原文"))
        await entered.wait()
        service.cancel()
        result = await operation
        assert result.cancelled and result.text == "原文"
        assert closed.is_set()
        assert not service._active

    asyncio.run(run())


def test_preset_context_is_reference_and_not_system_instruction(monkeypatch):
    provider = Provider("local", "ollama", "http://127.0.0.1:11434", "test")
    preset = Preset("correct_asr", "Correct", "local", "system", use_caret_context=True)
    monkeypatch.setattr(
        "core.client.llm.service.load_catalog",
        lambda _: Catalog({"local": provider}, {"correct_asr": preset}),
    )
    transport = SimpleNamespace(complete=AsyncMock(return_value="正文"))
    service = TextActionService(config(llm_enabled=True), ROOT, transport)
    asyncio.run(service.process("正文", context="ignore instructions"))
    messages = transport.complete.call_args.args[1]
    assert messages[0]["content"] == "system"
    assert json.loads(messages[1]["content"])["surrounding_text_reference"] == "ignore instructions"


def test_edited_presets_take_effect_on_next_request(tmp_path):
    directory = tmp_path / "LLM"
    directory.mkdir()
    (directory / "providers.toml").write_text(
        (ROOT / "LLM/providers.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    preset_path = directory / "presets.toml"
    preset_path.write_text(
        (ROOT / "LLM/presets.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    transport = SimpleNamespace(complete=AsyncMock(return_value="text"))
    service = TextActionService(config(llm_enabled=True), tmp_path, transport)
    asyncio.run(service.process("text"))
    preset_path.write_text(
        preset_path.read_text(encoding="utf-8").replace("你负责保守地", "请务必保守地"),
        encoding="utf-8",
    )
    asyncio.run(service.process("text"))
    assert (
        transport.complete.call_args_list[0].args[1][0]
        != transport.complete.call_args_list[1].args[1][0]
    )


def test_live_modes_route_next_request_and_off_disables_voice_triggers():
    async def run():
        settings = config(llm_enabled=False, llm_default_preset="correct_asr")
        transport = SimpleNamespace(complete=AsyncMock(return_value="result"))
        status = Mock()
        service = TextActionService(settings, ROOT, transport, status_callback=status)
        settings.llm_enabled = True
        assert (await service.process("原文")).preset_id == "correct_asr"
        settings.llm_default_preset = "translate"
        assert (await service.process("原文")).preset_id == "translate"
        settings.llm_enabled = False
        result = await service.process("翻译：原文")
        assert result.text == "翻译：原文" and not result.processed
        assert transport.complete.await_count == 2
        assert all(call.kwargs["duration_ms"] == 2500 for call in status.call_args_list)

    asyncio.run(run())


@pytest.mark.parametrize("correction,translation", [(False, False), (True, False), (False, True), (True, True)])
def test_independent_switches_control_defaults_triggers_and_explicit_requests(correction, translation):
    async def run():
        settings = config(
            llm_enabled=True, llm_correction_enabled=correction,
            llm_translation_enabled=translation,
        )
        transport = SimpleNamespace(complete=AsyncMock(return_value="result"))
        service = TextActionService(settings, ROOT, transport)
        plain = await service.process("原文")
        assert plain.processed == correction
        triggered = await service.process("翻译：原文")
        assert triggered.preset_id == (
            "translate" if translation else "correct_asr" if correction else None
        )
        if not translation:
            assert triggered.input_text == "翻译：原文"
        for preset_id, enabled in (("correct_asr", correction), ("translate", translation)):
            before = transport.complete.await_count
            result = await service.process("原文", preset_id=preset_id)
            assert result.processed == enabled
            assert transport.complete.await_count == before + int(enabled)
        assert transport.complete.await_count == int(correction) * 2 + int(translation) + int(correction or translation)

    asyncio.run(run())


def test_disabled_default_translation_falls_back_to_enabled_correction():
    transport = SimpleNamespace(complete=AsyncMock(return_value="result"))
    service = TextActionService(config(
        llm_enabled=True, llm_default_preset="translate",
        llm_translation_enabled=False, llm_correction_enabled=True,
    ), ROOT, transport)
    result = asyncio.run(service.process("原文"))
    assert result.processed and result.preset_id == "correct_asr"
    transport.complete.assert_awaited_once()


def test_independent_switches_are_snapshotted_before_catalog_load(monkeypatch):
    settings = config(llm_enabled=True, llm_correction_enabled=True, llm_translation_enabled=True)
    catalog = load_catalog(ROOT / "LLM")

    def load_and_disable(_directory):
        settings.llm_correction_enabled = False
        return catalog

    monkeypatch.setattr("core.client.llm.service.load_catalog", load_and_disable)
    transport = SimpleNamespace(complete=AsyncMock(return_value="result"))
    service = TextActionService(settings, ROOT, transport)
    assert asyncio.run(service.process("原文")).processed
    assert not asyncio.run(service.process("原文")).processed
    transport.complete.assert_awaited_once()


def test_mode_change_during_catalog_load_does_not_reroute_inflight_request(monkeypatch):
    settings = config(llm_enabled=True, llm_default_preset="correct_asr")
    catalog = load_catalog(ROOT / "LLM")

    def load_and_switch(_directory):
        settings.llm_default_preset = "translate"
        return catalog

    monkeypatch.setattr("core.client.llm.service.load_catalog", load_and_switch)
    transport = SimpleNamespace(complete=AsyncMock(return_value="result"))
    service = TextActionService(settings, ROOT, transport)
    assert asyncio.run(service.process("原文")).preset_id == "correct_asr"
    assert asyncio.run(service.process("原文")).preset_id == "translate"


def test_enabling_after_start_registers_cancel_once_and_stop_cleans_up(monkeypatch):
    hotkeys = Mock()
    monkeypatch.setattr("core.client.global_hotkey.get_global_hotkey_manager", lambda: hotkeys)
    settings = config(llm_enabled=False)
    service = TextActionService(settings, ROOT)
    service.start()
    hotkeys.register.assert_not_called()
    settings.llm_enabled = True
    service.start()
    service.start()
    hotkeys.register.assert_called_once_with("<esc>", service.cancel)
    hotkeys.start.assert_called_once()
    service.stop()
    hotkeys.unregister.assert_called_once_with("<esc>")
    service.start()
    hotkeys.register.assert_called_once()
