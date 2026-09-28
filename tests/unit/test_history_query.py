"""History queries use synthetic archives and never access personal recordings."""

import hashlib
import json
from pathlib import Path

import pytest

from core.settings_gui import history


def archive(root, day, text):
    path = history.day_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')
    return path


def detail(root, row):
    return history.read_history_entry(root, **{key: row[key] for key in ('day', 'offset', 'length', 'digest')})


def test_query_matches_existing_markdown_and_preserves_unicode(tmp_path):
    directory = tmp_path / 'records'
    content = ('### 12:10:05\n\n识别后的结果 Café\n\nTask: `one` · Outcome: `completed`\n\n'
               'Original transcription:\n\n原文\n\nSystem prompt:\n\nSynthetic prompt\n\n'
               '### 09:00:00\n\nEarlier result\n\n')
    path = archive(directory, '2026-09-01', content)
    archive(directory, '2026-08-31', '### 23:00:00\n\nOther month\n')
    archive(directory, '2026-09-02', '### 08:00:00\n\nNewer result\n')
    before = path.read_bytes()
    result = history.query_history(directory, '2026-09')
    assert [(row['day'], row['time']) for row in result['entries']] == [
        ('2026-09-02', '08:00:00'), ('2026-09-01', '12:10:05'), ('2026-09-01', '09:00:00')]
    result = history.query_history(directory, '2026-09', 'CAFÉ')
    assert result['total'] == 1
    assert 'Task:' not in result['entries'][0]['preview']
    record = detail(directory, result['entries'][0])
    assert '识别后的结果' in record['text'] and 'Original transcription:' in record['text']
    assert 'Synthetic prompt' in record['text'] and 'Earlier result' not in record['text']
    assert not record['truncated'] and path.read_bytes() == before


def test_pagination_and_payload_bounds(tmp_path):
    archive(tmp_path, '2026-09-10', ''.join(
        f'### 12:00:{i:02d}\n\nresult {i}\n\n' for i in range(45)))
    first = history.query_history(tmp_path, '2026-09')
    second = history.query_history(tmp_path, '2026-09', page=1)
    assert first['total'] == second['total'] == 45
    assert len(first['entries']) == 30 and len(second['entries']) == 15
    assert first['entries'][0]['time'] == '12:00:44'
    assert second['entries'][-1]['time'] == '12:00:00'
    assert len(json.dumps(first).encode()) < 2 * 1024 * 1024


@pytest.mark.parametrize('month', ['2026-13', '2026-2', '0000-01', '../2026', 9])
def test_invalid_month_is_rejected(tmp_path, month):
    with pytest.raises(ValueError):
        history.query_history(tmp_path, month)


@pytest.mark.parametrize('options', [{'page': -1}, {'page': True}, {'page': 10000},
                                    {'keyword': None}, {'keyword': 'a' * 201}])
def test_invalid_query_is_rejected(tmp_path, options):
    with pytest.raises(ValueError):
        history.query_history(tmp_path, '2026-09', **options)


@pytest.mark.parametrize('day', ['2026-02-30', '../../private', '2026-09-01/other', None])
def test_invalid_record_path_is_rejected(tmp_path, day):
    with pytest.raises(ValueError):
        history.day_path(tmp_path, day)


def test_missing_archive_does_not_create_files(tmp_path):
    directory = tmp_path / 'not-created'
    assert history.query_history(directory, '2024-02')['total'] == 0
    assert not directory.exists()


def test_changed_or_deleted_entry_is_not_silently_replaced(tmp_path):
    path = archive(tmp_path, '2026-09-01', '### 12:00:00\n\nOriginal entry\n')
    row = history.query_history(tmp_path, '2026-09')['entries'][0]
    path.write_text('### 12:00:00\n\nReplaced entry\n', encoding='utf-8')
    with pytest.raises(ValueError):
        detail(tmp_path, row)
    path.unlink()
    with pytest.raises(ValueError):
        detail(tmp_path, row)


def test_old_plain_day_record_and_literal_markup(tmp_path):
    content = '<script>never execute</script>\n[link](https://example.invalid)\nLegacy text'
    archive(tmp_path, '2024-02-29', content)
    result = history.query_history(tmp_path, '2024-02')
    assert result['total'] == 1 and result['entries'][0]['time'] == ''
    assert detail(tmp_path, result['entries'][0])['text'] == content


def test_large_archives_are_bounded_and_explicitly_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(history, 'DAY_BYTES', 120)
    monkeypatch.setattr(history, 'QUERY_BYTES', 150)
    archive(tmp_path, '2026-09-30', '### 01:00:00\n\n' + 'x' * 200 + '\n### 23:00:00\n\nNewest\n')
    archive(tmp_path, '2026-09-29', '### 12:00:00\n\nPrevious day\n' * 10)
    result = history.query_history(tmp_path, '2026-09')
    assert result['limited']
    assert result['entries'][0]['time'] == '23:00:00'
    assert detail(tmp_path, result['entries'][0])['text'].endswith('Newest')


def test_long_details_are_bounded_with_visible_truncation(tmp_path, monkeypatch):
    archive(tmp_path, '2026-09-01', '### 12:00:00\n\n' + '汉' * 1000)
    monkeypatch.setattr(history, 'DETAIL_CHARS', 100)
    row = history.query_history(tmp_path, '2026-09')['entries'][0]
    result = detail(tmp_path, row)
    assert result['truncated'] and len(result['text']) == 100


def test_link_outside_archive_is_not_read(tmp_path, monkeypatch):
    root = tmp_path / 'records'
    path = archive(root, '2026-09-01', '### 12:00:00\n\nPublic fixture\n')
    original = Path.resolve
    monkeypatch.setattr(Path, 'resolve', lambda self, *a, **kw:
                        tmp_path / 'outside.md' if self == path else original(self, *a, **kw))
    result = history.query_history(root, '2026-09')
    assert result['skipped'] == 1 and result['total'] == 0


def test_entry_bounds_reject_arbitrary_reads(tmp_path):
    archive(tmp_path, '2026-09-01', 'test')
    with pytest.raises(ValueError):
        history.read_history_entry(tmp_path, '2026-09-01', 0, history.DAY_BYTES + 1,
                                   hashlib.sha256(b'test').hexdigest())


def test_bom_and_windows_newlines_keep_the_first_entry(tmp_path):
    path = archive(tmp_path, '2026-09-01', '')
    path.write_bytes(b'\xef\xbb\xbf### 01:00:00\r\n\r\nFirst\r\n### 02:00:00\r\n\r\nSecond\r\n')
    result = history.query_history(tmp_path, '2026-09')
    assert result['total'] == 2
    assert detail(tmp_path, result['entries'][1])['text'].endswith('First')


def test_dates_and_keyword_are_independent_and_composable(tmp_path):
    for day, word in [('2026-08-31', 'alpha'), ('2026-09-01', 'Alpha'), ('2026-09-02', 'beta')]:
        archive(tmp_path, day, '### 12:00:00\n\n' + word)
    assert history.query_history(tmp_path)['total'] == 3
    assert history.query_history(tmp_path, keyword='ALPHA')['total'] == 2
    assert history.query_history(tmp_path, date_from='2026-09-01')['total'] == 2
    assert history.query_history(tmp_path, date_to='2026-08-31')['total'] == 1
    result = history.query_history(tmp_path, date_from='2026-08-31', date_to='2026-09-01', keyword='alpha')
    assert [row['day'] for row in result['entries']] == ['2026-09-01', '2026-08-31']
    assert history.query_history(tmp_path, date_from='2026-09-02', keyword='alpha')['total'] == 0


@pytest.mark.parametrize('options', [{'date_from': '2026-02-29'}, {'date_to': '../private'},
                                    {'date_from': True}, {'date_from': '2026-09-02', 'date_to': '2026-09-01'}])
def test_date_boundaries_are_validated(tmp_path, options):
    with pytest.raises(ValueError):
        history.query_history(tmp_path, **options)


def test_page_is_clamped_when_archive_shrinks(tmp_path):
    archive(tmp_path, '2026-09-28', '### 12:00:00\n\nRemaining record')
    result = history.query_history(tmp_path, page=1)
    assert result['page'] == 0 and len(result['entries']) == 1


def test_discovery_is_bounded_and_does_not_follow_escaped_directories(tmp_path, monkeypatch):
    directory = tmp_path / 'records'
    archive(directory, '2026-09-28', '### 12:00:00\n\nSynthetic')
    monkeypatch.setattr(history, 'MAX_PATHS', 2)
    assert history.query_history(directory)['limited']
    monkeypatch.setattr(history, 'MAX_PATHS', 20000)
    original = Path.resolve
    monkeypatch.setattr(Path, 'resolve', lambda self, *a, **kw:
                        tmp_path / 'outside' if self == directory / '2026' else original(self, *a, **kw))
    result = history.query_history(directory)
    assert result['skipped'] == 1 and result['total'] == 0


def test_structured_stages_match_archive_writer_and_keep_saved_prompt(tmp_path):
    from datetime import datetime
    from core.client.diary.diary_writer import DiaryWriter
    DiaryWriter(tmp_path).write('Final result', datetime(2026, 9, 28, 12).timestamp(),
                               original='Initial recognition', action_input='Cleaned input',
                               action_output='LLM response', task_id='task', request_id='request',
                               preset_id='synthetic', system_prompt='Actual saved prompt', reference_text='Saved context')
    record = detail(tmp_path, history.query_history(tmp_path)['entries'][0])
    assert record['stages'] == {'final': 'Final result', 'original': 'Initial recognition',
                               'input': 'Cleaned input', 'output': 'LLM response', 'prompt': 'Actual saved prompt',
                               'context': 'Saved context', 'outcome': 'completed', 'preset': 'synthetic', 'request': 'request'}


def test_missing_stages_are_not_invented_and_deduplicated_output_is_reconstructed():
    from core.settings_gui.history_detail import parse_record
    legacy = parse_record('### 12:00:00\n\nPlain result')
    assert legacy['final'] == 'Plain result'
    assert all(legacy[key] is None for key in ('original', 'input', 'output', 'prompt', 'context', 'request'))
    saved = ('### 12:00:00\n\nFinal\n\nTask: `task` · Outcome: `completed`\n\n'
             'Action input:\n\nInput\n\nPreset: `synthetic` · Request: `id`\n')
    assert parse_record(saved)['output'] == 'Final'
    assert parse_record(saved, truncated=True)['output'] is None
    assert parse_record(saved.replace('completed', 'fallback'))['output'] is None


def cost_database(root, record, month='2026-09'):
    import sqlite3
    from contextlib import closing
    path = root / 'llm-costs' / (month + '.sqlite3')
    path.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, started TEXT, record TEXT)')
        db.execute('INSERT INTO requests VALUES (?, ?, ?)', ('request', month, json.dumps(record)))
    return path


@pytest.mark.parametrize('source', ['provider_reported', 'rate_estimate', 'token_estimate', 'possible_cost'])
def test_single_request_cost_preserves_provenance_without_private_metadata(tmp_path, source):
    from core.settings_gui.history_detail import request_cost
    path = cost_database(tmp_path, {'provider': 'example', 'model': 'model', 'status': 'completed', 'elapsed_ms': 1530,
        'rate': {'source': 'private-metadata'}, 'endpoint_hash': 'private-metadata',
        'accounting': {'amount': '0.000123', 'currency': 'USD', 'cost_source': source, 'usage_source': 'provider',
                       'usage': {'input_tokens': 123, 'output_tokens': 40, 'cached_tokens': 20}}}, month='2026-10')
    before = path.read_bytes()
    cost = request_cost(tmp_path, 'LLM', '2026-09-30', 'request')
    assert cost['state'] == 'found' and cost['amount'] == '0.000123' and cost['source'] == source
    assert cost['usage'] == {'input_tokens': 123, 'output_tokens': 40, 'cached_tokens': 20}
    assert 'private-metadata' not in repr(cost) and path.read_bytes() == before


def test_missing_and_corrupt_costs_do_not_create_data_or_claim_zero(tmp_path):
    from core.settings_gui.history_detail import request_cost
    assert request_cost(tmp_path, 'LLM', '2026-09-28', '') == {'state': 'unlinked'}
    assert request_cost(tmp_path, 'LLM', '2026-09-28', 'request') == {'state': 'missing'}
    assert list(tmp_path.iterdir()) == []
    path = cost_database(tmp_path, {'accounting': {'amount': None, 'cost_source': 'unknown'}})
    assert request_cost(tmp_path, 'LLM', '2026-09-28', 'request')['amount'] is None
    path.write_bytes(b'not a database')
    assert request_cost(tmp_path, 'LLM', '2026-09-28', 'request') == {'state': 'unavailable'}


def test_oversized_cost_record_is_not_loaded(tmp_path):
    from core.settings_gui.history_detail import request_cost
    cost_database(tmp_path, {'extra': 'x' * 70000})
    assert request_cost(tmp_path, 'LLM', '2026-09-28', 'request') == {'state': 'missing'}
