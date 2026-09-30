"""Synthetic storage, clock, consent and migration boundaries; no devices or providers."""

import asyncio
from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.activity.runtime import ActivityRecorder
from core.activity.schema import METRICS
from core.activity.store import NAME, connection, import_legacy, records_for_month, write_request
from core.activity.queries import task_detail, timing_summary
from core.client.llm.config import Catalog, Preset, Provider
from core.client.llm.service import TextActionService
from core.llm_accounting.ledger import CostLedger, read_month
from core.llm_accounting.usage import UsageObservation


def recorder(tmp_path, **values):
    return ActivityRecorder(tmp_path, SimpleNamespace(save_runtime_statistics=True, **values))


def sample_record(identifier='r'):
    return {'schema_version': 1, 'id': identifier, 'started_at': '2026-09-29T00:00:00+00:00',
            'finished_at': None, 'status': 'unfinished', 'provider': 'synthetic', 'model': 'model',
            'preset': 'correct_asr', 'elapsed_ms': None, 'rate': None,
            'accounting': {'usage': {}, 'invalid_usage': False, 'amount': None, 'currency': None,
                           'cost_source': 'unknown', 'usage_source': 'unknown'}}


def legacy(directory, records):
    directory.mkdir(exist_ok=True)
    path = directory / '2026-09.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE requests(id TEXT PRIMARY KEY,started TEXT,record TEXT)')
        db.executemany('INSERT INTO requests VALUES (?,?,?)',
                       [(r['id'], r['started_at'], json.dumps(r)) for r in records])
    return path


def test_registry_schema_constraints_and_future_version(tmp_path):
    path = tmp_path / NAME
    with connection(path) as db:
        assert db.execute('PRAGMA foreign_keys').fetchone() == (1,)
        assert db.execute('SELECT count(*) FROM metric_definitions').fetchone()[0] == len(METRICS)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError):
        with connection(path):
            pass
    with pytest.raises(ValueError):
        with connection(path, readonly=True):
            pass


def test_cold_wake_uses_callback_clock_and_warm_is_not_zero(tmp_path):
    runtime = recorder(tmp_path)
    t = runtime.epoch
    runtime.begin('cold', now=t)
    runtime.ready('cold', wake_started=t + 1_000_000, ready_at=t + 13_345_000)
    runtime.ready('cold', wake_started=t, ready_at=t + 900_000_000)
    runtime.finish('cold', 'completed')
    runtime.begin('warm', now=t)
    runtime.ready('warm', warm=True, ready_at=t)
    runtime.finish('warm', 'completed')
    runtime.close()
    assert runtime.write_errors == 0
    detail = task_detail(tmp_path / 'llm-costs', 'cold')
    values = {row['metric']: row['value_us'] for row in detail['operations']}
    assert values['microphone.wake'] == 12345
    assert values['dictation.start_wait'] == 13345
    summary = timing_summary(tmp_path, {})
    wake = next(row for row in summary['groups'] if row['metric'] == 'microphone.wake')
    assert wake['count'] == 1 and wake['missing'] == 1 and wake['median'] == 12.345


def test_post_stop_excludes_automatic_enter_and_missing_llm_is_explicit(tmp_path):
    runtime = recorder(tmp_path)
    t = runtime.epoch
    runtime.begin('task', now=t)
    for name, ms in [('stop', 10), ('asr', 110), ('processing', 140), ('llm_returned', 150),
                     ('insert', 160), ('insert_done', 170), ('enter', 171), ('enter_done', 900)]:
        runtime.mark('task', name, now=t + ms * 1_000_000)
    runtime.llm_skipped('task')
    runtime.finish('task', 'completed')
    runtime.close()
    assert runtime.write_errors == 0
    rows = {row['metric']: row for row in task_detail(tmp_path / 'llm-costs', 'task')['operations']}
    assert rows['dictation.post_stop']['value_us'] == 160000
    assert rows['dictation.transcribe_wait']['value_us'] == 100000
    assert rows['dictation.result_queue']['value_us'] == 30000
    assert rows['llm.total']['availability'] == 'not_applicable'


def test_statistics_disabled_creates_no_file_or_worker(tmp_path):
    runtime = ActivityRecorder(tmp_path, SimpleNamespace(save_runtime_statistics=False))
    runtime.begin('task')
    runtime.mark('task', 'stop')
    runtime.finish('task', 'completed')
    runtime.close()
    assert runtime.thread is None and not (tmp_path / 'llm-costs').exists()


def test_shutdown_marks_owned_pending_tasks_only_and_preserves_other_run(tmp_path):
    first, second = recorder(tmp_path), recorder(tmp_path)
    first.begin('first')
    second.begin('second')
    assert first.flush() and second.flush()
    first.close()
    assert task_detail(tmp_path / 'llm-costs', 'first')['task']['state'] == 'interrupted'
    assert task_detail(tmp_path / 'llm-costs', 'second')['task']['state'] == 'running'
    second.close()


def test_failed_wake_not_counted_as_success_and_terminal_is_idempotent(tmp_path):
    runtime = recorder(tmp_path)
    runtime.begin('task')
    runtime.mark('task', 'wake')
    runtime.finish('task', 'failed', 'MicrophoneResumeFailed')
    runtime.finish('task', 'completed')
    runtime.close()
    summary = timing_summary(tmp_path, {})
    wake = next(row for row in summary['groups'] if row['metric'] == 'microphone.wake')
    assert wake['count'] == 0 and wake['failed'] == 1
    assert summary['outcomes'] == {'failed': 1}


def test_explicit_import_preserves_source_and_deduplicates_queries(tmp_path):
    record = sample_record()
    record.update(status='completed', finished_at='2026-09-29T00:00:01+00:00', elapsed_ms=1000)
    record['accounting'].update(amount='0.000001', currency='USD', cost_source='provider_reported')
    source = legacy(tmp_path, [record])
    before = source.read_bytes()
    assert import_legacy(tmp_path) == {'imported': 1, 'existing': 0}
    assert import_legacy(tmp_path) == {'imported': 0, 'existing': 1}
    assert source.read_bytes() == before
    report = read_month(tmp_path, '2026-09')
    assert report['requests'] == 1
    assert report['currencies']['USD']['planning_total'] == '0.000001'
    with connection(tmp_path / NAME, readonly=True) as db:
        assert db.execute('SELECT origin,operation_id FROM llm_requests').fetchone() == ('legacy_v1', None)
        assert db.execute('SELECT count(*) FROM measurements').fetchone() == (0,)


def test_conflicting_import_rolls_back_whole_month(tmp_path):
    record = sample_record('z')
    legacy(tmp_path, [sample_record('a'), record])
    with connection(tmp_path / NAME) as db:
        write_request(db, {**record, 'model': 'different'})
    with pytest.raises(ValueError):
        import_legacy(tmp_path)
    with connection(tmp_path / NAME, readonly=True) as db:
        assert db.execute('SELECT id FROM llm_requests').fetchall() == [('z',)]


@pytest.mark.parametrize('cost_enabled', [False, True])
def test_llm_statistics_and_cost_share_database_without_content(tmp_path, monkeypatch, cost_enabled):
    provider = Provider('synthetic', 'openai', 'http://localhost:1', 'synthetic-model')
    monkeypatch.setattr('core.client.llm.service.load_catalog', lambda _: Catalog(
        {'synthetic': provider}, {'correct_asr': Preset('correct_asr', 'Correction', 'synthetic', 'PRIVATE_PROMPT')}))
    async def complete(*args):
        return 'PRIVATE_OUTPUT'
    config = SimpleNamespace(llm_enabled=True, llm_cost_tracking=cost_enabled,
                             save_runtime_statistics=True, save_diagnostic_logs=False, log_level='CRITICAL')
    runtime = ActivityRecorder(tmp_path, config)
    service = TextActionService(config, tmp_path, SimpleNamespace(complete=complete))
    service.activity = runtime
    runtime.begin('task')
    result = asyncio.run(service.process('PRIVATE_INPUT', task_id='task'))
    assert result.processed
    runtime.finish('task', 'completed')
    runtime.close()
    assert runtime.write_errors == 0
    with connection(tmp_path / 'llm-costs' / NAME, readonly=True) as db:
        row = db.execute('SELECT id,operation_id,accounting_enabled,record FROM llm_requests').fetchone()
        assert row[0] == result.request_id and row[1] and row[2] == int(cost_enabled)
        assert 'PRIVATE_' not in row[3]
        assert db.execute('SELECT count(*) FROM requests').fetchone() == (int(cost_enabled),)
        metrics = {row[0] for row in db.execute('SELECT metric FROM measurements')}
        assert {'llm.total', 'llm.prepare', 'llm.request'} <= metrics


def test_terminal_cost_write_does_not_change_finished_bill(tmp_path):
    ledger = CostLedger(tmp_path, tmp_path / 'LLM')
    provider = Provider('synthetic', 'openai', 'http://localhost:1', 'model')
    ticket, _ = ledger.prepare(provider, [], 10, 'r', 'p', datetime(2026, 9, 29, tzinfo=timezone.utc))
    observed = UsageObservation(sent=True, reported_cost='0.01', cost_currency='USD')
    ledger.finish(ticket, observed, 'completed', None, 15)
    observed.reported_cost = '999'
    ledger.finish(ticket, observed, 'failed', None, 100)
    records, _, _ = records_for_month(tmp_path / 'llm-costs', '2026-09')
    assert records[0]['status'] == 'completed'
    assert records[0]['accounting']['amount'] == '0.01'


def test_write_failure_is_reported_without_replacing_task_result(tmp_path, monkeypatch):
    import core.activity.runtime as module
    def broken(*args, **kwargs):
        raise OSError('PRIVATE_PATH')
    monkeypatch.setattr(module, 'connection', broken)
    runtime = recorder(tmp_path)
    runtime.begin('task')
    runtime.finish('task', 'completed')
    runtime.close()
    assert runtime.write_errors > 0 and not runtime.tasks


def test_documented_sql_exactly_matches_executable_schema():
    from pathlib import Path
    from core.activity.schema import DDL
    document = (Path(__file__).resolve().parents[2] / 'docs/reference/activity-database.md').read_text(encoding='utf-8')
    assert document.split('```sql\n', 1)[1].split('```', 1)[0].strip() == DDL.strip()


def test_invalid_clock_boundary_is_explicit_loss(tmp_path):
    runtime = recorder(tmp_path)
    runtime.begin('task')
    runtime.measure('task', 'dictation.insert', 2000, 1000)
    runtime.finish('task', 'completed')
    runtime.close()
    rows = task_detail(tmp_path / 'llm-costs', 'task')['operations']
    row = next(row for row in rows if row['metric'] == 'dictation.insert')
    assert row['availability'] == 'lost' and row['value_us'] is None
    assert runtime.write_errors == 0


@pytest.mark.parametrize('extra', [{'text': 'PRIVATE_TEXT'}, {'schema_version': 99},
                                  {'accounting': {'usage': {'input_tokens': True}}}])
def test_import_rejects_untrusted_or_future_payloads_without_copying(tmp_path, extra):
    legacy(tmp_path, [{**sample_record(), **extra}])
    with pytest.raises(ValueError):
        import_legacy(tmp_path)
    if (tmp_path / NAME).exists():
        with connection(tmp_path / NAME, readonly=True) as db:
            assert db.execute('SELECT count(*) FROM llm_requests').fetchone() == (0,)


def test_queue_loss_is_bounded_and_not_silent(tmp_path, monkeypatch):
    import threading
    runtime = ActivityRecorder(tmp_path, SimpleNamespace(save_runtime_statistics=True), capacity=1)
    entered, release = threading.Event(), threading.Event()
    write = runtime._write
    def slow(db, kind, data):
        if kind == 'begin':
            entered.set()
            assert release.wait(3)
        return write(db, kind, data)
    monkeypatch.setattr(runtime, '_write', slow)
    runtime.begin('task')
    assert entered.wait(3)
    runtime.mark('task', 'stop')
    for _ in range(20):
        runtime.measure('task', 'dictation.insert', runtime.epoch, runtime.epoch + 1000)
    assert runtime.dropped > 0 and runtime.queue.qsize() == 1
    release.set()
    assert runtime.flush()
    runtime.finish('task', 'completed')
    runtime.close()
    with connection(tmp_path / 'llm-costs' / NAME, readonly=True) as db:
        assert db.execute('SELECT dropped FROM runs').fetchone()[0] > 0


def test_cost_only_mode_has_no_task_or_measurement_records(tmp_path):
    ledger = CostLedger(tmp_path, tmp_path / 'LLM')
    ticket, _ = ledger.prepare(Provider('p', 'openai', 'http://localhost:1', 'm'), [], 10, 'r', 'p')
    ledger.finish(ticket, UsageObservation(), 'not_sent', 'missing_api_key', 1)
    with connection(tmp_path / 'llm-costs' / NAME, readonly=True) as db:
        for table in ('tasks', 'operations', 'measurements', 'runs'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM requests').fetchone() == (1,)


def test_short_llm_phases_use_high_resolution_duration_clock(monkeypatch):
    import logging
    from core.client.llm.diagnostics import RequestDiagnostics
    moments = iter([100.0, 100.0001, 100.0003])
    monkeypatch.setattr('core.client.llm.diagnostics.time.perf_counter', lambda: next(moments))
    diagnostic = RequestDiagnostics(logging.Logger('synthetic-clock', logging.CRITICAL), 'request')
    diagnostic.stage('preparation')
    diagnostic.finish('completed')
    assert diagnostic.elapsed_ms == .3
    assert 0 < diagnostic.stage_ms['configuration'] < 1
    assert 0 < diagnostic.stage_ms['preparation'] < 1


def test_unregistered_metric_reports_contract_failure(tmp_path):
    runtime = recorder(tmp_path)
    runtime.begin('task')
    runtime.measure('task', 'undefined.metric', 1, 2)
    runtime.finish('task', 'completed')
    runtime.close()
    assert runtime.write_errors == 1
    detail = task_detail(tmp_path / 'llm-costs', 'task')
    assert not detail['task']['complete']
    assert all(row['metric'] != 'undefined.metric' for row in detail['operations'])


def test_request_failure_does_not_relabel_completed_preparation(tmp_path, monkeypatch):
    provider = Provider('p', 'openai', 'http://localhost:1', 'm')
    monkeypatch.setattr('core.client.llm.service.load_catalog', lambda _: Catalog(
        {'p': provider}, {'correct_asr': Preset('correct_asr', 'Correction', 'p', 'system')}))
    async def complete(*args):
        raise ConnectionError('synthetic failure')
    runtime = recorder(tmp_path)
    service = TextActionService(SimpleNamespace(llm_enabled=True, llm_cost_tracking=False),
                                tmp_path, SimpleNamespace(complete=complete))
    service.activity = runtime
    runtime.begin('task')
    result = asyncio.run(service.process('synthetic text', task_id='task'))
    assert result.error and result.text == 'synthetic text'
    runtime.finish('task', 'fallback')
    runtime.close()
    rows = {row['metric']: row for row in task_detail(tmp_path / 'llm-costs', 'task')['operations']}
    assert rows['llm.prepare']['outcome'] == 'completed'
    assert rows['llm.request']['outcome'] == rows['llm.total']['outcome'] == 'failed'
