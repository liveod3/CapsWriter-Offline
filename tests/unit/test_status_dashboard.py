"""Dashboard reads synthetic archives/ledgers without collecting new content."""

from datetime import datetime, timezone, timedelta
import json
import sqlite3

import pytest

from core.client.diary.diary_writer import DiaryWriter
from core.settings_gui.status_data import dashboard, copy_result, usage_summary


NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
CONFIG = {'transcript_dir': 'records', 'llm_config_dir': 'LLM', 'llm_cost_tracking': True,
          'save_transcripts': True, 'save_llm_records': False}


def request_record(started=NOW, *, usage=None, source='rate_estimate', amount='0.002', currency='USD'):
    return {'started_at': started.isoformat(), 'accounting': {
        'usage': {'input_tokens': 100, 'output_tokens': 20, 'cached_tokens': 50, 'reasoning_tokens': 10}
        if usage is None else usage,
        'invalid_usage': False, 'cost_source': source, 'amount': amount, 'currency': currency,
        'estimated_usage': {'input_tokens': 500, 'output_tokens': 200}}}


def write_ledger(root, month, records):
    folder = root / 'llm-costs'
    folder.mkdir(exist_ok=True)
    with sqlite3.connect(folder / (month + '.sqlite3')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, started TEXT, record TEXT)')
        for index, record in enumerate(records):
            db.execute('INSERT INTO requests VALUES (?, ?, ?)', (str(index), record.get('started_at', ''), json.dumps(record)))


def test_dashboard_unknown_is_not_zero_and_does_not_create_files(tmp_path):
    result = dashboard(tmp_path, {**CONFIG, 'save_transcripts': False, 'llm_cost_tracking': False}, now=NOW)
    assert result['entries'] == [] and result['today_count'] == 0
    assert result['usage']['state'] == 'empty' and not result['usage']['tracking_enabled']
    assert not result['saving_enabled'] and result['usage']['currencies'] == {}
    assert list(tmp_path.iterdir()) == []


def test_usage_keeps_provenance_currencies_and_unknown_tokens_separate(tmp_path):
    records = [request_record(), request_record(source='provider_reported', amount='0.05'),
               request_record(usage={}, source='possible_cost', amount='0.01'),
               request_record(currency='CNY', amount='1.20'),
               request_record(usage={'total_tokens': 17}, source='unknown', amount=None),
               request_record(NOW - timedelta(days=1), source='token_estimate', amount='0.004')]
    write_ledger(tmp_path, '2026-09', records)
    result = usage_summary(tmp_path, CONFIG, NOW)
    assert result['tokens_today'] == 377  # Cached/reasoning tokens are subsets, not extra tokens.
    assert result['unknown_token_requests'] == 1
    assert result['month_requests'] == 6 and result['requests_today'] == 5
    assert result['unknown_cost_requests'] == 1
    assert result['currencies']['USD'] == {'provider_reported': '0.05', 'rate_estimate': '0.002',
                                           'possible_cost': '0.01', 'token_estimate': '0.004'}
    assert result['currencies']['CNY']['rate_estimate'] == '1.20'
    assert 'estimated_usage' not in repr(result)


def test_usage_includes_adjacent_month_at_local_boundary_and_skips_malformed(tmp_path):
    now = datetime(2026, 10, 1, 2, tzinfo=timezone(timedelta(hours=8)))
    write_ledger(tmp_path, '2026-09', [request_record(datetime(2026, 9, 30, 18, tzinfo=timezone.utc)),
                                     {'started_at': 'invalid'}, request_record(NOW)])
    result = usage_summary(tmp_path, CONFIG, now)
    assert result['month_requests'] == result['requests_today'] == 1
    assert result['tokens_today'] == 120 and result['skipped'] == 1


def test_ledger_read_is_bounded_and_unavailable_is_explicit(tmp_path, monkeypatch):
    write_ledger(tmp_path, '2026-09', [request_record() for _ in range(4)])
    monkeypatch.setattr('core.settings_gui.status_data.MAX_REQUESTS', 2)
    result = usage_summary(tmp_path, CONFIG, NOW)
    assert result['limited'] and result['month_requests'] == 2
    with sqlite3.connect(tmp_path / 'llm-costs/2026-09.sqlite3') as db:
        db.execute('DROP TABLE requests')
    assert usage_summary(tmp_path, CONFIG, NOW)['state'] == 'unavailable'


def test_history_latest_ten_today_filter_and_full_copy_are_read_only(tmp_path):
    local = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    writer = DiaryWriter(tmp_path / 'records')
    for index in range(12):
        writer.write('Older saved result ' + str(index), (local - timedelta(days=1, minutes=index)).timestamp())
    full = 'Full final result ' + 'multiline content\n' * 30
    path = writer.write(full, local.timestamp(), original='Synthetic ASR', action_input='Synthetic input',
                        action_output='Synthetic LLM', system_prompt='PRIVATE PROMPT', reference_text='PRIVATE CONTEXT')
    original = path.read_bytes()
    recent = dashboard(tmp_path, CONFIG, now=local)
    assert recent['today_count'] == 1 and len(recent['entries']) == 10
    assert 'PRIVATE' not in repr(recent)
    entry = recent['entries'][0]
    reference = {key: entry[key] for key in ('day', 'offset', 'length', 'digest')}
    assert copy_result(tmp_path, CONFIG, reference) == full.strip()
    assert path.read_bytes() == original
    assert len(dashboard(tmp_path, CONFIG, period='today', now=local)['entries']) == 1
    path.write_bytes(original.replace(b'Full final', b'Changed final'))
    with pytest.raises(ValueError):
        copy_result(tmp_path, CONFIG, reference)


def test_long_history_copy_is_rejected_instead_of_copying_a_partial_result(tmp_path):
    local = datetime.now().astimezone()
    DiaryWriter(tmp_path / 'records').write('x' * 65000, local.timestamp())
    entry = dashboard(tmp_path, CONFIG, now=local)['entries'][0]
    with pytest.raises(ValueError):
        copy_result(tmp_path, CONFIG, {key: entry[key] for key in ('day', 'offset', 'length', 'digest')})
