"""Exercise terminal status entry points, including objects reused across reloads."""

import ast
import io
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest
from rich.console import Console
from rich.status import Status as RichStatus

from core.i18n import ENGLISH, Notice, set_language, tr


def test_reused_recording_and_receiving_spinners_follow_current_language(monkeypatch):
    from core.client.shortcut.task import ShortcutTask
    from core.server.connection.ws_recv import status_mic

    # No live refresh thread, microphone or socket is needed to render the labels.
    monkeypatch.setattr(RichStatus, 'start', Mock())
    monkeypatch.setattr(RichStatus, 'stop', Mock())
    monkeypatch.setattr(status_mic, 'started', False)
    monkeypatch.setattr(status_mic, 'status', status_mic.status)
    monkeypatch.setattr(status_mic, '_spinner', status_mic._spinner)
    recorder = ShortcutTask(SimpleNamespace(), SimpleNamespace(key='ctrl_r'))._status
    for language in ('zh-CN', 'en', 'zh-CN', 'en'):
        set_language(language)
        for status, key in ((recorder, 'mic.recording'), (status_mic, 'server.receiving_mic')):
            status.start()
            output = io.StringIO()
            Console(file=output, force_terminal=False).print(status.renderable)
            assert tr(key) in output.getvalue()
            if language == 'en':
                assert not re.search(r'[\u4e00-\u9fff]', output.getvalue())
            status.stop()


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_model_startup_rule_uses_selected_language(language, monkeypatch):
    from core.server.worker import process_manager as module

    set_language(language)
    console = Mock()
    monkeypatch.setattr(module, 'console', console)
    manager = module.ProcessManager(SimpleNamespace(
        state=SimpleNamespace(queue_out=SimpleNamespace(get=lambda **_: True)), stop=Mock(),
    ))
    manager.is_alive = True
    manager._process = SimpleNamespace(is_alive=lambda: True)
    manager._wait_for_models()
    console.rule.assert_called_once_with(tr('server.ready'))
    manager.app.stop.assert_not_called()


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_model_loading_status_uses_worker_config_without_loading_models(language, monkeypatch):
    from core.server.worker import model_loader as module

    set_language('zh-CN' if language == 'en' else 'en')
    monkeypatch.setattr(module.Config, 'ui_language', language, raising=False)
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace())
    monkeypatch.setattr(module.EngineFactory, 'create_asr_engine', Mock(return_value=SimpleNamespace(
        capabilities={module.EngineCapabilities.PUNC, module.EngineCapabilities.TIMESTAMPS},
    )))
    console = MagicMock()
    monkeypatch.setattr(module, 'console', console)
    module.ModelLoader().load()
    console.status.assert_called_once_with(
        tr('server.loading_modules', locale=language),
        spinner='bouncingBall', spinner_style='yellow',
    )


def test_reload_notices_follow_effective_language_and_leave_history_intact(monkeypatch):
    from core.client import app as module
    from core.client.state import ClientState

    config = SimpleNamespace(ui_language='en', llm_enabled=False)
    monkeypatch.setattr(module, 'Config', config)
    monkeypatch.setattr('core.ui.tray.refresh_language', Mock())
    output = io.StringIO()
    monkeypatch.setattr(module, 'console', Console(file=output, width=160))
    client = module.CapsWriterClient.__new__(module.CapsWriterClient)
    client.state = ClientState()
    client._stopping = client._file_active = False
    client.config_reload = SimpleNamespace(apply=lambda: ('ui_language',))
    set_language('en')
    expected = []
    for language in ('zh-CN', 'en'):
        pending = Notice('config.pending', fields='ui_language')
        expected.append(pending.localized())
        client._report_config(pending)
        config.ui_language = language
        client.apply_config_reload()
        expected.append(tr('config.applied', fields='ui_language'))
    assert output.getvalue().splitlines() == expected
    assert expected[-1] == 'Configuration applied: ui_language'


def test_product_console_entry_points_have_no_embedded_chinese_labels():
    """Guard direct labels in print/rule/status calls; logs and tools are separate."""
    root = Path(__file__).resolve().parents[2]
    paths = list((root / 'core/client').rglob('*.py'))
    paths += list((root / 'core/server').rglob('*.py'))
    for path in paths:
        if 'engines' in path.parts:
            continue  # Derived inference code is outside product UI resources.
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if not isinstance(node, ast.Call):
                continue
            is_status = isinstance(node.func, ast.Name) and node.func.id == 'Status'
            is_console = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {'print', 'rule', 'status', 'input'}
                and ast.unparse(node.func.value) in {'console', 'self.console'}
            )
            if not (is_status or is_console):
                continue
            for arg in node.args:
                literals = [arg] if isinstance(arg, ast.Constant) else (
                    arg.values if isinstance(arg, ast.JoinedStr) else []
                )
                for part in literals:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        assert not re.search(r'[\u4e00-\u9fff]', part.value), (path, node.lineno)
            if is_status:
                for keyword in node.keywords:
                    if keyword.arg == 'message_id':
                        assert ast.literal_eval(keyword.value) in ENGLISH
