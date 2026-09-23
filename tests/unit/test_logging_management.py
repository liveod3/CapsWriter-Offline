"""Process ownership, explicit text capture, retention and failure boundaries."""

import asyncio
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.diagnostics import BufferedDiagnosticHandler, DiagnosticFileHandler
from core.logger import ConsoleFeedbackFilter, Logger, log_content


def records(root):
    return [json.loads(line) for p in root.rglob('*.jsonl*') for line in p.read_text(encoding='utf-8').splitlines()]


@pytest.mark.parametrize('persist,text,context', [(a, b, c) for a in (False, True)
                                               for b in (False, True) for c in (False, True)])
def test_diagnostic_switch_matrix_and_console_boundary(tmp_path, monkeypatch, persist, text, context):
    from config_client import ClientConfig
    for key, value in dict(save_diagnostic_logs=persist, diagnostic_include_text=text,
                           diagnostic_include_context=context, diagnostic_text_max_chars=5).items():
        monkeypatch.setattr(ClientConfig, key, value, raising=False)
    name = 'matrix'
    logger = Logger.setup(name, tmp_path, level='DEBUG')
    console = io.StringIO()
    console_handler = logging.StreamHandler(console)
    console_handler.addFilter(ConsoleFeedbackFilter())
    logger.addHandler(console_handler)
    try:
        logger.info('metadata')
        log_content(logger, 'test.text', task_id='task', input_text='PRIVATE_TEXT',
                    context='CARET_REFERENCE', api_key='CREDENTIAL')
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        Logger._loggers.pop(name)
    rows = records(tmp_path)
    assert bool(rows) == persist
    copies = [r for r in rows if 'content' in r]
    assert bool(copies) == (persist and text)
    if copies:
        assert copies[0]['content']['input_text'] == {'text': 'PRIVA', 'chars': 12, 'truncated': True}
        assert ('context' in copies[0]['content']) == context
    assert 'PRIVA' not in console.getvalue() and 'CARET' not in console.getvalue()
    assert 'CREDENTIAL' not in json.dumps(rows)


def test_server_persistence_does_not_read_client_switch(tmp_path, monkeypatch):
    from config_client import ClientConfig
    from config_server import ServerConfig
    monkeypatch.setattr(ClientConfig, 'save_diagnostic_logs', False)
    monkeypatch.setattr(ServerConfig, 'save_diagnostic_logs', True, raising=False)
    previous = Logger._loggers.pop('server', None)
    existing = logging.getLogger('server').handlers
    logging.getLogger('server').handlers = []
    try:
        logger = Logger.setup('server', tmp_path)
        logger.info('server remains independent')
        for handler in list(logger.handlers):
            handler.close()
        assert len(records(tmp_path)) == 1
    finally:
        logging.getLogger('server').handlers = existing
        Logger._loggers.pop('server', None)
        if previous:
            Logger._loggers['server'] = previous


def test_spawned_processes_own_distinct_files_and_complete_lines(tmp_path):
    code = '''import logging, sys
from core.diagnostics import BufferedDiagnosticHandler, DiagnosticFileHandler
sink = DiagnosticFileHandler(sys.argv[1], 'server')
writer = BufferedDiagnosticHandler(sink)
for i in range(30):
    writer.handle(logging.LogRecord('server', 20, 'fixture.py', 1, 'entry %s', (i,), None))
writer.close()
'''
    workers = [subprocess.Popen([sys.executable, '-c', code, str(tmp_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
    try:
        for worker in workers:
            out, err = worker.communicate(timeout=20)
            assert worker.returncode == 0, (out, err)
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
    rows = records(tmp_path)
    assert len(rows) == 90
    assert len({r['pid'] for r in rows}) == 3
    assert len({r['run_id'] for r in rows}) == 3
    assert all(r['timestamp'][-6] in '+-' for r in rows)


def test_retention_skips_active_sessions_and_unowned_files(tmp_path):
    old = DiagnosticFileHandler(tmp_path, 'client', retention_days=1)
    row = logging.LogRecord('client', 20, '', 0, 'synthetic', (), None)
    old.handle(row)
    os.utime(old.path, (1, 1))
    unrelated = old.path.parent / 'notes.jsonl'
    unrelated.write_text('keep', encoding='utf-8')
    checker = DiagnosticFileHandler(tmp_path, 'client', retention_days=1)
    checker.cleanup()
    assert old.path.exists()
    old.close()
    checker.cleanup()
    assert not old.path.exists()
    assert unrelated.read_text(encoding='utf-8') == 'keep'
    checker.close()


def test_rotation_bounds_and_budget_even_when_age_expiry_is_disabled(tmp_path):
    old = DiagnosticFileHandler(tmp_path, 'client', retention_days=0, max_bytes=400, backup_count=2)
    for i in range(20):
        old.handle(logging.LogRecord('client', 20, '', 0, 'x' * 120, (), None))
    old.close()
    assert len(list(old.path.parent.glob('*.jsonl*'))) <= 3
    old.path.write_text('x' * (2 * 1024 * 1024), encoding='utf-8')
    checker = DiagnosticFileHandler(tmp_path, 'client', retention_days=0, budget_mb=1)
    checker.cleanup()
    assert not old.path.exists()
    checker.close()


def test_full_queue_does_not_block_and_reports_drops(tmp_path):
    entered, release = threading.Event(), threading.Event()
    sink = DiagnosticFileHandler(tmp_path, 'client')
    original = sink.emit

    def blocked(record):
        entered.set()
        release.wait(5)
        original(record)

    sink.emit = blocked
    writer = BufferedDiagnosticHandler(sink, capacity=1)
    record = logging.LogRecord('client', 20, '', 0, 'synthetic', (), None)
    try:
        writer.handle(record)
        assert entered.wait(2)
        writer.handle(record)
        start = time.monotonic()
        for _ in range(50):
            writer.handle(record)
        assert time.monotonic() - start < 0.5
        assert writer.dropped == 50
    finally:
        release.set()
        writer.close()
    assert any('overflow' in row['message'] for row in records(tmp_path))


def test_disk_failure_does_not_echo_sensitive_record(tmp_path, monkeypatch, capsys):
    sink = DiagnosticFileHandler(tmp_path, 'client')
    monkeypatch.setattr(sink, '_open', Mock(side_effect=OSError('PRIVATE_TEXT')))
    sink.handle(logging.LogRecord('client', 20, '', 0, 'PRIVATE_TEXT', (), None))
    sink.close()
    assert sink.failures == 1
    assert 'PRIVATE_TEXT' not in capsys.readouterr().err


@pytest.mark.parametrize('save_context', [False, True])
def test_llm_records_keep_only_actual_sent_reference(tmp_path, monkeypatch, save_context):
    from core.client.llm.config import Catalog, Preset, Provider
    from core.client.llm.service import TextActionService
    provider = Provider('fixture', 'openai', 'https://synthetic.invalid', 'test')
    preset = Preset('correct_asr', 'Correction', 'fixture', 'synthetic prompt', use_caret_context=True)
    monkeypatch.setattr('core.client.llm.service.load_catalog',
                        lambda p: Catalog({'fixture': provider}, {'correct_asr': preset}))
    config = SimpleNamespace(llm_enabled=True, llm_cost_tracking=False,
                             save_llm_records=True, save_llm_context=save_context)
    transport = SimpleNamespace(complete=AsyncMock(return_value='result'))
    result = asyncio.run(TextActionService(config, tmp_path, transport).process('input', context='x' * 4000))
    assert result.reference_text == ('x' * 3500 if save_context else '')
    assert result.system_prompt == 'synthetic prompt'
    assert result.request_id


def test_record_written_before_failed_insertion(tmp_path, monkeypatch):
    from core.client.output import result_processor as module
    from core.client.state import ClientState
    from core.client.llm.service import TextResult
    from core.client.diary.diary_writer import DiaryWriter
    for key, value in dict(save_transcripts=True, save_audio=False, save_llm_records=False,
                           traditional_convert=False, enter_apps=[]).items():
        monkeypatch.setattr(module.Config, key, value)
    monkeypatch.setattr(module, 'get_active_window_info', lambda: {})
    app = SimpleNamespace(state=ClientState(), progress=Mock(), diary=DiaryWriter(tmp_path),
                          llm=SimpleNamespace(process=AsyncMock(return_value=TextResult('kept', 'kept'))),
                          output=SimpleNamespace(output=AsyncMock(side_effect=OSError('insertion failed'))))
    async def run():
        with pytest.raises(OSError):
            await module.ResultProcessor(app)._handle_final(SimpleNamespace(task_id='id', text='kept', time_start=0))
    asyncio.run(run())
    assert 'kept' in next(tmp_path.rglob('*.md')).read_text(encoding='utf-8')


def test_same_task_id_keeps_socket_identity_and_exception_payload_out(tmp_path):
    from core.logger import diagnostic_event
    sink = DiagnosticFileHandler(tmp_path, 'server')
    logger = logging.Logger('fixture', level=logging.DEBUG)
    logger.addHandler(sink)
    for socket in ('first', 'second'):
        diagnostic_event(logger, 'asr.task_finished', task_id='same', socket_id=socket, chars=3)
    try:
        raise RuntimeError('SECRET_EXCEPTION_PAYLOAD')
    except RuntimeError:
        logger.error('Task failed', exc_info=True)
    sink.close()
    rows = records(tmp_path)
    assert [r['socket_id'] for r in rows[:2]] == ['first', 'second']
    assert rows[-1]['exception_type'] == 'RuntimeError'
    assert rows[-1]['traceback']
    assert 'SECRET_EXCEPTION_PAYLOAD' not in json.dumps(rows)


def test_daily_history_append_from_multiple_processes(tmp_path):
    code = '''import sys
from pathlib import Path
from core.client.diary.diary_writer import DiaryWriter
writer = DiaryWriter(Path(sys.argv[1]))
for i in range(10):
    writer.write('synthetic', 0, task_id=sys.argv[2] + '-' + str(i))
'''
    # DiaryWriter's client-package import may initialize a logger; point the child
    # configuration at the same temporary sandbox without touching real settings.
    prefix = "import sys, types; c=types.ModuleType('config_client'); c.BASE_DIR=sys.argv[1]; c.ClientConfig=type('ClientConfig', (), {'log_level':'INFO', 'save_diagnostic_logs':False}); sys.modules['config_client']=c;\n"
    workers = [subprocess.Popen([sys.executable, '-c', prefix + code, str(tmp_path), str(i)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(3)]
    try:
        for worker in workers:
            out, err = worker.communicate(timeout=20)
            assert worker.returncode == 0, (out, err)
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
    content = next(tmp_path.rglob('*.md')).read_text(encoding='utf-8')
    assert content.count('### ') == 30
    for i in range(3):
        for j in range(10):
            assert content.count(f'Task: `{i}-{j}`') == 1


def test_reader_filters_content_and_never_echoes_malformed_payload(tmp_path, capsys):
    from scripts.read_logs import main
    path = tmp_path / 'fixture.jsonl'
    path.write_text(json.dumps({'level': 'INFO', 'task_id': 'task', 'request_id': 'request',
                                'message': 'safe', 'content': {'input': 'SENSITIVE'}}) + '\nBAD_SECRET', encoding='utf-8')
    main([str(path), '--json'])
    text = capsys.readouterr().out
    assert 'SENSITIVE' not in text and 'BAD_SECRET' not in text
    assert 'reader.invalid_record' in text
    main([str(path), '--request', 'request', '--content', '--json'])
    assert 'SENSITIVE' in capsys.readouterr().out
    main([str(path), '--task', 'other'])
    assert not capsys.readouterr().out
