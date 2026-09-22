"""Regression coverage for all maintained display and diagnostic entry points."""

import ast
import io
import logging
import pickle
import re
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rich.console import Console
from rich.logging import RichHandler

from core.i18n import CATALOGS, ENGLISH, Notice, bind_shared_language, set_language, tr
from core.i18n.logging import LocalizedFormatter


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_all_catalog_templates_render_and_preserve_logging_parameters(language):
    set_language(language)
    for key, template in ENGLISH.items():
        fields = {name: 2 for _, name, _, _ in Formatter().parse(template) if name}
        if any(name.startswith('task.') for name in fields):
            fields = {'task': SimpleNamespace(
                percentage=50.0, fields=dict(total_audio='00:10', processed='00:05', remaining='00:05'),
            )}
        rendered = tr(key, **fields)
        assert rendered != key
        # Logging applies percent parameters after localizing a Notice.
        percent = r'%(?:\([^)]+\))?[-+0 #]*\d*(?:\.\d+)?[sdrif]'
        assert re.findall(percent, CATALOGS[language][key]) == re.findall(percent, template), key
        if language == 'en' and key != 'language.chinese':
            assert not re.search(r'[\u4e00-\u9fff]', rendered), key


def test_catalog_sources_have_no_duplicate_ids():
    for name in ['en.py', 'zh_cn.py']:
        tree = ast.parse((ROOT / 'core/i18n' / name).read_text(encoding='utf-8'))
        catalog = next(node.value for node in tree.body if isinstance(node, ast.Assign))
        keys = [ast.literal_eval(key) for key in catalog.keys]
        assert len(keys) == len(set(keys)), name


@pytest.mark.parametrize('console_first', [True, False])
def test_console_localizes_without_changing_shared_record_or_file_sinks(console_first):
    terminal, archive = io.StringIO(), io.StringIO()
    console = RichHandler(console=Console(file=terminal, width=200), show_time=False,
                          show_path=False, show_level=False, markup=False)
    console.setFormatter(LocalizedFormatter())
    file_sink = logging.StreamHandler(archive)
    file_sink.setFormatter(logging.Formatter('%(message)s'))
    logger = logging.Logger('synthetic-localization', logging.DEBUG)
    logger.handlers = [console, file_sink] if console_first else [file_sink, console]
    message = Notice('diagnostic.portaudio_compat.device_refresh_failed')
    for language in ['zh-CN', 'en', 'zh-CN']:
        set_language(language)
        logger.warning(message, 'SyntheticError')
    assert archive.getvalue().splitlines() == ['Device refresh failed: SyntheticError'] * 3
    assert terminal.getvalue().count('设备刷新失败：SyntheticError') == 2
    assert terminal.getvalue().count('Device refresh failed: SyntheticError') == 1
    assert str(message) == 'Device refresh failed: %s'


def test_nested_validation_notices_localize_without_translating_user_values():
    reason = Notice('validation.server_manager.serverconfig_must_be_a_positive_integer', value0='port')
    message = Notice('config.rejected', reason=ValueError(reason))
    restored = pickle.loads(pickle.dumps(message))
    set_language('zh-CN')
    assert '必须是正整数' in restored.localized()
    assert 'must be a positive integer' in str(restored)
    value = '用户文件名 invalid choice: keep me {value0}'
    assert value in tr('terminal.srt_from_txt.written', value0=value)
    assert value in tr('terminal.srt_from_txt.written', locale='en', value0=value)


@pytest.mark.parametrize(('arguments', 'expected'), [
    (['transcribe'], '缺少必需参数'),
    (['transcribe', '--format'], '需要一个参数值'),
    (['rebuild-srt', '--text', 'synthetic.txt'], '缺少必需参数'),
    (['mic', '--unknown'], '无法识别的参数'),
    (['unknown-command'], '无效选项'),
    (['transcribe', '--recursive', '--no-recursive', 'synthetic.wav'], '不能与参数'),
    (['mic', '--help=value'], '不接受显式参数值'),
])
def test_parser_generated_errors_follow_locale(arguments, expected, monkeypatch, capsys):
    from core.client.cli import Config, parse_client_command
    monkeypatch.setattr(Config, 'ui_language', 'zh-CN', raising=False)
    with pytest.raises(SystemExit) as stopped:
        parse_client_command(arguments)
    assert stopped.value.code == 2
    assert expected in capsys.readouterr().err
    monkeypatch.setattr(Config, 'ui_language', 'en')
    with pytest.raises(SystemExit):
        parse_client_command(arguments)
    assert not re.search(r'[\u4e00-\u9fff]', capsys.readouterr().err)


def test_server_reload_publishes_to_existing_workers(monkeypatch):
    from core.server import app as module
    from core.server.worker.process_manager import ProcessManager
    manager = ProcessManager(SimpleNamespace())
    server = module.CapsWriterServer.__new__(module.CapsWriterServer)
    server.process_manager = manager
    server.config_reload = SimpleNamespace(apply=lambda: ('ui_language',))
    server._report_config = Mock()
    monkeypatch.setattr('core.ui.tray.refresh_language', Mock())
    bind_shared_language(manager._ui_language)
    for language in ['zh-CN', 'en', 'zh-CN']:
        monkeypatch.setattr(module.Config, 'ui_language', language, raising=False)
        server.apply_config_reload()
        assert tr('server.ready') == tr('server.ready', locale=language)
        assert manager._ui_language.value == int(language == 'zh-CN')


def _language_worker(shared, pipe):
    bind_shared_language(shared)
    for _ in range(3):
        pipe.recv()
        pipe.send(tr('server.ready'))
    pipe.close()


def test_spawned_worker_observes_language_changes_without_restart():
    from multiprocessing import get_context
    context = get_context('spawn')
    shared = context.Value('i', 0)
    parent, child = context.Pipe()
    process = context.Process(target=_language_worker, args=(shared, child))
    process.start()
    child.close()
    try:
        for language in ['zh-CN', 'en', 'zh-CN']:
            shared.value = int(language == 'zh-CN')
            parent.send(None)
            assert parent.poll(10), 'Language worker did not respond'
            assert parent.recv() == tr('server.ready', locale=language)
        process.join(10)
        assert process.exitcode == 0
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()


def _literal_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ''.join(_literal_text(part) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return _literal_text(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)):
        return _literal_text(node.left) + _literal_text(node.right)
    return ''


def test_display_and_logging_sinks_do_not_embed_product_sentences():
    """Check English as well as Chinese; retain content fixtures and upstream code."""
    failures = []
    for path in (ROOT / 'core').rglob('*.py'):
        if {'i18n', 'export', 'gguf', 'zhconv'}.intersection(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = ast.unparse(node.func)
            is_log = fn.startswith(('logger.', 'logging.')) and fn.rsplit('.', 1)[-1] in {
                'debug', 'info', 'warning', 'error', 'exception', 'critical',
            }
            is_display = fn in {
                'print', 'input', 'console.print', 'console.rule', 'console.status',
                'console.input', 'self.console.print', 'reporter.print', 'vprint',
                'show_status_hint', 'Status',
            }
            values = list(node.args[:1]) if is_log or is_display else []
            if fn.startswith(('ttk.', 'tk.')) or fn in {'Panel', 'Panel.fit'}:
                values.extend(kw.value for kw in node.keywords if kw.arg in {'text', 'title'})
            for value in values:
                literal = _literal_text(value)
                literal = re.sub(r'\[/?[a-zA-Z][a-zA-Z0-9_. #]*\]|\[/\]|%s', '', literal)
                if re.search(r'[a-zA-Z\u4e00-\u9fff]', literal):
                    failures.append((str(path.relative_to(ROOT)), node.lineno, literal))
    assert not failures, failures


def test_no_hidden_chinese_labels_in_maintained_runtime_sources():
    """Catch labels assigned before display; exclude recognition data explicitly."""
    failures = []
    for path in (ROOT / 'core').rglob('*.py'):
        relative = path.relative_to(ROOT).as_posix()
        if {'i18n', 'export', 'gguf', 'zhconv', 'chinese_itn'}.intersection(path.parts):
            continue
        if path.name in {'chinese_itn.py', 'prompt_builder.py', 'language.py'}:
            continue  # Recognition rules, task prompts and accepted ASR language aliases.
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if not re.search(r'[\u4e00-\u9fff]', node.value) or isinstance(parents.get(node), ast.Expr):
                continue  # Comments/docstrings are covered by the internal-English TODO.
            if node.value == '====解码有误，强制熔断====' and path.name in {'asr.py', 'pipeline.py'}:
                continue  # Existing recognition-result sentinel; not an interface label.
            owner = node
            while owner in parents and not isinstance(owner, ast.Assign):
                owner = parents[owner]
            names = {target.id for target in owner.targets if isinstance(target, ast.Name)} if isinstance(owner, ast.Assign) else set()
            if relative == 'core/tools/format_tools.py' and 'test_cases' in names:
                continue  # Text transformation fixtures must retain their input.
            if relative == 'core/tools/format_tools.py' and node.value.startswith('(?ix)'):
                continue  # Comments inside a verbose recognition regex.
            if relative == 'core/client/output/text_output.py' and node.value.startswith('[一-'):
                continue  # Word-count regex.
            failures.append((relative, node.lineno, node.value))
    assert not failures, failures


def test_verbose_engine_notice_keeps_archive_english(capsys):
    # Load only the utility function, without importing ONNX or native backends.
    path = ROOT / 'core/server/engines/fun_asr_gguf/inference/utils.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'vprint')
    from core.i18n import localize_notice
    logger = Mock()
    namespace = dict(logger=logger, localize_notice=localize_notice)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    set_language('zh-CN')
    notice = Notice('terminal.models.loading_audio_encoder')
    namespace['vprint'](notice)
    assert '加载音频编码器' in capsys.readouterr().out
    assert str(logger.info.call_args.args[0]) == '[1/6] Loading audio encoder...'
