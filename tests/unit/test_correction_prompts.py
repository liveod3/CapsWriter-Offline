"""Prompt composition boundaries, compatibility and immutable request resolution."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.correction_prompts import (
    CorrectionOptions, assemble_correction, inspect_prompt, resolve_preset, snapshot_options,
)
from core.llm_config import Catalog, Preset, Provider, load_catalog
from core.settings import SettingsService


@pytest.fixture
def prompt_files(tmp_path):
    directory = tmp_path / 'LLM'
    directory.mkdir()
    public = Path(__file__).resolve().parents[2] / 'LLM'
    for name in ('providers.template.toml', 'presets.toml'):
        (directory / name).write_bytes((public / name).read_bytes())
    return directory


@pytest.mark.parametrize('level', ['minimal', 'natural', 'fluent'])
def test_all_modules_disabled_retain_explicit_limits_at_every_level(level):
    prompt = assemble_correction(CorrectionOptions(level, False, False, False, False, False), use_context=True)
    for instruction in (
        '数字格式模块关闭', '标点模块关闭', 'filler 模块关闭',
        '英文术语还原模块关闭', '同音纠错模块关闭',
        '整个 transcript 的完整处理结果', '关闭模块的限制优先于编辑强度',
    ):
        assert instruction in prompt
    assert '230 个' not in prompt
    assert '插入位置标点规则' not in prompt
    assert '“油箱”可改为“邮箱”' not in prompt


@pytest.mark.parametrize('level,label', [
    ('minimal', '最小修改'), ('natural', '自然整理'), ('fluent', '流畅改写'),
])
def test_strength_does_not_change_content_contract_or_module_choices(level, label):
    options = CorrectionOptions(level=level, english=True)
    prompt = assemble_correction(options, use_context=True)
    assert prompt == assemble_correction(options, use_context=True)
    assert '编辑强度：' + label in prompt
    assert all(value in prompt for value in ('前导零', '条件', '否定', '未说完', '不扩写、概括'))
    assert '英文术语还原模块：' in prompt
    assert '仅当转写语义' in prompt
    assert '不能仅凭音近猜测' in prompt
    assert '插入位置标点规则' in prompt


@pytest.mark.parametrize('name,enabled_marker,disabled_marker', [
    ('numbers', '数字格式模块：', '数字格式模块关闭'),
    ('punctuation', '标点模块：', '标点模块关闭'),
    ('fillers', 'filler 模块：', 'filler 模块关闭'),
    ('english', '英文术语还原模块：', '英文术语还原模块关闭'),
    ('homophones', '同音纠错模块：', '同音纠错模块关闭'),
])
def test_each_module_can_be_enabled_and_disabled_independently(name, enabled_marker, disabled_marker):
    enabled = assemble_correction(replace(CorrectionOptions(), **{name: True}))
    disabled = assemble_correction(replace(CorrectionOptions(), **{name: False}))
    assert enabled_marker in enabled and disabled_marker not in enabled
    assert disabled_marker in disabled and enabled_marker not in disabled


@pytest.mark.parametrize('field,value', [
    ('level', 'maximum'), ('level', []), ('numbers', 1), ('fillers', 'false'), ('english', None),
])
def test_invalid_options_rejected_with_controlled_errors(field, value):
    with pytest.raises(ValueError):
        assemble_correction(replace(CorrectionOptions(), **{field: value}))


@pytest.mark.parametrize('client_gate,preset_gate,punctuation', [
    (False, False, True), (False, True, True), (True, False, True),
    (True, True, True), (True, True, False),
])
def test_actual_request_enforces_context_gates_and_prompt_modules(
    tmp_path, monkeypatch, client_gate, preset_gate, punctuation,
):
    from core.client.llm.service import TextActionService

    preset = Preset('correct_asr', 'Synthetic correction', 'p', '',
                    use_caret_context=preset_gate, prompt_mode='correction')
    provider = Provider('p', 'ollama', 'http://localhost:11434', 'fixture')
    monkeypatch.setattr('core.client.llm.service.load_catalog', lambda _: Catalog({'p': provider}, {preset.id: preset}))
    config = SimpleNamespace(llm_enabled=True, llm_cost_tracking=False,
                             caret_context_enabled=client_gate, llm_correction_punctuation=punctuation,
                             save_llm_records=True)
    transport = SimpleNamespace(complete=AsyncMock(return_value='synthetic complete result'))
    result = asyncio.run(TextActionService(config, tmp_path, transport).process('synthetic input', context='reference'))
    transport.complete.assert_awaited_once()
    messages = transport.complete.call_args.args[1]
    assert len(messages) == 2 and result.processed
    assert ('surrounding_text_reference' in json.loads(messages[1]['content'])) == (client_gate and preset_gate)
    assert ('插入位置标点规则' in messages[0]['content']) == (client_gate and preset_gate and punctuation)
    assert result.system_prompt == messages[0]['content']
    # Composing a request must not mutate a catalog shared with other requests.
    assert preset.system_prompt == '' and preset.use_caret_context == preset_gate


def test_legacy_prompt_and_translation_ignore_correction_options(prompt_files):
    path = prompt_files / 'presets.toml'
    path.write_text(path.read_text(encoding='utf-8').replace(
        'prompt_mode = "correction"', 'system_prompt = "Do exactly the custom task."'), encoding='utf-8')
    catalog = load_catalog(prompt_files)
    bad_options = CorrectionOptions(level='unused-invalid-option')
    custom = resolve_preset(catalog.presets['correct_asr'], bad_options, context_enabled=True)
    translation = resolve_preset(catalog.presets['translate'], bad_options, context_enabled=True)
    assert custom.system_prompt == 'Do exactly the custom task.'
    assert custom.prompt_mode == 'custom'
    assert translation.system_prompt == catalog.presets['translate'].system_prompt
    assert 'Translate the transcript' in translation.system_prompt


@pytest.mark.parametrize('replacement', [
    'prompt_mode = "unknown"',
    'prompt_mode = "correction"\nsystem_prompt = "Do not silently ignore custom content."',
    'prompt_mode = "custom"',
])
def test_ambiguous_or_incomplete_modes_fail_before_requests(prompt_files, replacement):
    path = prompt_files / 'presets.toml'
    path.write_text(path.read_text(encoding='utf-8').replace('prompt_mode = "correction"', replacement), encoding='utf-8')
    with pytest.raises(ValueError):
        load_catalog(prompt_files)


def test_options_and_context_are_fixed_before_catalog_await(tmp_path, monkeypatch):
    from core.client.llm.service import TextActionService

    config = SimpleNamespace(llm_enabled=True, llm_cost_tracking=False, caret_context_enabled=False,
                             llm_correction_level='minimal', llm_correction_numbers=False)
    provider = Provider('p', 'ollama', 'http://localhost:11434', 'fixture')
    preset = Preset('correct_asr', 'Correction', 'p', '', use_caret_context=True, prompt_mode='correction')

    def load(_):
        config.llm_correction_level = 'fluent'
        config.llm_correction_numbers = True
        config.caret_context_enabled = True
        return Catalog({'p': provider}, {'correct_asr': preset})

    monkeypatch.setattr('core.client.llm.service.load_catalog', load)
    transport = SimpleNamespace(complete=AsyncMock(return_value='result'))
    service = TextActionService(config, tmp_path, transport)
    asyncio.run(service.process('input', context='reference'))
    first = transport.complete.call_args.args[1]
    assert '最小修改' in first[0]['content'] and '数字格式模块关闭' in first[0]['content']
    assert 'surrounding_text_reference' not in json.loads(first[1]['content'])
    asyncio.run(service.process('input', context='reference'))
    second = transport.complete.call_args.args[1]
    assert '流畅改写' in second[0]['content'] and '数字格式模块：' in second[0]['content']
    assert json.loads(second[1]['content'])['surrounding_text_reference'] == 'reference'


def test_invalid_composition_falls_back_without_request(prompt_files):
    from core.client.llm.service import TextActionService

    transport = SimpleNamespace(complete=AsyncMock())
    config = SimpleNamespace(llm_enabled=True, llm_cost_tracking=False, llm_correction_level='unsupported')
    result = asyncio.run(TextActionService(config, prompt_files.parent, transport).process('whole input'))
    assert result.text == 'whole input' and not result.processed
    assert result.error == 'ValueError'
    transport.complete.assert_not_awaited()


def test_shared_editor_validates_saves_and_previews_at_publication_boundary(prompt_files, attach_client_operations):
    path = prompt_files.parent / 'config_client.py'
    path.write_text("class ClientConfig:\n    llm_enabled = False\n    ui_language = 'en'\n", encoding='utf-8')
    config = SimpleNamespace(llm_enabled=False, ui_language='en')
    app = SimpleNamespace(base_dir=prompt_files.parent, llm=SimpleNamespace(directory=prompt_files))
    operations = attach_client_operations(app, config)

    async def run():
        original = await operations.inspect_prompt()
        snapshot = await operations.read_settings()
        changes = {'llm_correction_level': 'fluent', 'llm_correction_numbers': False}
        preview = await operations.validate_settings(changes, revision=snapshot.revision)
        assert 'ClientConfig.llm_correction_level' in preview.pending
        await operations.save_settings(changes, revision=snapshot.revision)
        assert (await operations.inspect_prompt()).system_prompt == original.system_prompt
        app.config_reload.poll()
        app.config_reload.poll()
        app.config_reload.apply()
        current = await operations.inspect_prompt()
        assert current.options.level == 'fluent' and current.options.numbers is False
        assert '流畅改写' in current.system_prompt and '数字格式模块关闭' in current.system_prompt
        assert config.llm_enabled is False

    asyncio.run(run())
    service = SettingsService.standalone(path)
    stable = path.read_bytes()
    with pytest.raises(ValueError):
        service.save({'llm_correction_level': 'invalid'}, revision=service.read().revision)
    assert path.read_bytes() == stable


def test_preview_matches_request_without_capturing_or_sending(prompt_files, monkeypatch):
    from core.client.llm.service import TextActionService

    network = Mock(side_effect=AssertionError('preview must not send'))
    monkeypatch.setattr('httpx.AsyncClient', network)
    config = SimpleNamespace(llm_enabled=True, llm_cost_tracking=False, caret_context_enabled=True,
                             llm_correction_fillers=False)
    preview = inspect_prompt(prompt_files, config)
    network.assert_not_called()
    transport = SimpleNamespace(complete=AsyncMock(return_value='result'))
    asyncio.run(TextActionService(config, prompt_files.parent, transport).process('input'))
    assert transport.complete.call_args.args[1][0]['content'] == preview.system_prompt
    assert 'system_prompt' not in repr(preview)
    assert snapshot_options(SimpleNamespace()) == CorrectionOptions()
