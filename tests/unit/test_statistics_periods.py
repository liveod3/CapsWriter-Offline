"""Calendar scope, metric cohorts and accounting reconciliation with synthetic data."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from core.activity.periods import calendar_window
from core.activity.queries import timing_summary
from core.activity.store import NAME, connection, write_request
from core.settings_gui.status_data import SUMMARY_BYTES, bound_summary, dashboard, usage_summary


NOW = datetime(2026, 10, 1, 12, tzinfo=timezone(timedelta(hours=8)))


def seed(directory):
    with connection(directory / NAME) as db:
        db.execute("INSERT INTO runs VALUES ('run','client','test','2026-09-01T00:00:00.000000+00:00',"
                   "NULL,'test',1,0,0)")


def task(db, identifier, stamp, outcome='completed'):
    db.execute('INSERT INTO tasks(task_id,run_id,started_at,state,outcome) VALUES (?,?,?,?,?)',
               (identifier, 'run', stamp, 'finished' if outcome else 'running', outcome))


def measurement(db, identifier, value, availability='observed', version=1, outcome='completed'):
    operation = identifier + str(version)
    db.execute('INSERT INTO operations(operation_id,task_id,run_id,kind,outcome) VALUES (?,?,?,?,?)',
               (operation, identifier, 'run', 'microphone.wake', outcome))
    db.execute('INSERT INTO measurements VALUES (?,?,?,?,?,?,?,?,?)',
               (operation, operation, 'microphone.wake', version, value, availability, 'client', 1, None))


def bill(identifier, stamp, model='model', *, amount='0.002', currency='USD', source='rate_estimate'):
    return {'schema_version': 1, 'id': identifier, 'started_at': stamp, 'status': 'completed',
            'provider': 'synthetic', 'model': model, 'accounting': {'cost_source': source,
                'amount': amount, 'currency': currency, 'usage': {'input_tokens': 100, 'output_tokens': 20}}}


def test_calendar_windows_cross_year_and_keep_half_open_boundaries():
    window = calendar_window('7d', NOW.replace(month=1, day=2))
    assert window['date_from'] == '2025-12-27' and window['date_to'] == '2026-01-02'
    assert window['utc_start'] == '2025-12-26T16:00:00.000000+00:00'
    assert window['utc_end'] == '2026-01-02T16:00:00.000000+00:00'
    with pytest.raises(ValueError):
        calendar_window('all', NOW)
    with pytest.raises(ValueError):
        calendar_window('today', NOW.replace(tzinfo=None))


@pytest.mark.parametrize('period,stamp,first', [
    ('week', '2026-01-01', '2025-12-29'),
    ('week', '2026-01-04', '2025-12-29'),
    ('week', '2026-01-05', '2026-01-05'),
    ('month', '2024-02-29', '2024-02-01'),
    ('month', '2026-10-01', '2026-10-01'),
    ('year', '2024-12-31', '2024-01-01'),
    ('year', '2026-01-01', '2026-01-01'),
])
def test_calendar_periods_start_at_local_calendar_boundaries(period, stamp, first):
    now = datetime.fromisoformat(stamp + 'T12:00:00+08:00')
    window = calendar_window(period, now)
    assert window['date_from'] == first and window['date_to'] == stamp
    assert datetime.fromisoformat(window['utc_start']) == datetime.fromisoformat(first + 'T00:00:00+08:00')
    assert datetime.fromisoformat(window['utc_end']) == (now + timedelta(days=1)).replace(hour=0)


@pytest.mark.parametrize('period', ['week', 'month', 'year'])
def test_calendar_period_filters_runtime_and_costs_at_same_boundaries(tmp_path, period):
    directory = tmp_path / 'llm-costs'
    seed(directory)
    window = calendar_window(period, NOW)
    start, end = (datetime.fromisoformat(window[key]) for key in ('utc_start', 'utc_end'))
    with connection(directory / NAME) as db:
        for identifier, moment in (('before', start - timedelta(microseconds=1)), ('first', start),
                                   ('last', end - timedelta(microseconds=1)), ('after', end)):
            stamp = moment.isoformat(timespec='microseconds')
            task(db, identifier, stamp)
            measurement(db, identifier, 123000)
            write_request(db, bill(identifier, stamp))
    result = dashboard(tmp_path, {}, stats_period=period, now=NOW)
    assert result['timings']['count'] == result['usage']['requests'] == 2
    assert result['usage']['tokens'] == 240
    assert not result['usage']['limited'] and not result['timings']['limited']


def test_year_accounting_reads_all_months_and_reports_shared_budget(tmp_path, monkeypatch):
    directory = tmp_path / 'llm-costs'
    seed(directory)
    with connection(directory / NAME) as db:
        for month in range(1, 13):
            write_request(db, bill(str(month), f'2026-{month:02d}-15T12:00:00+00:00'))
    now = NOW.replace(month=12, day=31)
    window = calendar_window('year', now)
    result = usage_summary(tmp_path, {}, now, window)
    assert result['requests'] == 12 and result['tokens'] == 1440 and not result['limited']
    monkeypatch.setattr('core.settings_gui.status_data.MAX_SCOPE_REQUESTS', 3)
    result = usage_summary(tmp_path, {}, now, window)
    assert result['requests'] == 3 and result['tokens'] == 360 and result['limited']


def test_period_separates_missing_skipped_pending_and_metric_versions(tmp_path):
    directory = tmp_path / 'llm-costs'
    seed(directory)
    window = calendar_window('today', NOW)
    with connection(directory / NAME) as db:
        db.execute("INSERT INTO metric_definitions SELECT name,2,unit,start_boundary,end_boundary,aggregation "
                   "FROM metric_definitions WHERE name='microphone.wake'")
        for identifier in ('cold', 'warm', 'lost', 'pending'):
            task(db, identifier, window['utc_start'], None if identifier == 'pending' else 'completed')
        task(db, 'outside', window['utc_end'])
        measurement(db, 'outside', 9000000)
        measurement(db, 'cold', 1000)
        measurement(db, 'cold', 9000, version=2)
        measurement(db, 'warm', None, 'not_applicable', outcome='skipped')
        measurement(db, 'lost', None, 'lost')
    result = timing_summary(tmp_path, {}, window)
    assert result['count'] == 4 and not result['limited']
    assert result['health']['pending'] == 1 and result['health']['not_applicable'] == 1
    assert result['health']['missing'] == 1 and result['health']['write_errors'] == 0
    groups = [row for row in result['groups'] if row['metric'] == 'microphone.wake']
    assert [(row['version'], row['median']) for row in groups] == [(1, 1), (2, 9)]
    assert result['latest']['values']['microphone.wake'] == 1
    assert groups[0]['unavailable'] == groups[0]['not_applicable'] == 1
    assert len(result['tasks']) == 4 and 'outside' not in repr(result)


def test_calendar_query_covers_more_than_latest_500_and_discloses_caps(tmp_path, monkeypatch):
    directory = tmp_path / 'llm-costs'
    seed(directory)
    window = calendar_window('today', NOW)
    with connection(directory / NAME) as db:
        for index in range(510):
            task(db, str(index), window['utc_start'])
        db.execute("UPDATE runs SET dropped=2,write_errors=1 WHERE run_id='run'")
    result = timing_summary(tmp_path, {}, window)
    assert result['count'] == 510 and not result['limited'] and len(result['tasks']) == 50
    assert result['health']['dropped'] == 2 and result['health']['write_errors'] == 1
    monkeypatch.setattr('core.activity.queries.MAX_TASKS', 5)
    result = timing_summary(tmp_path, {}, window)
    assert result['count'] == 5 and result['limited']


def test_statistics_accounting_range_deduplicates_legacy_and_keeps_models(tmp_path):
    directory = tmp_path / 'llm-costs'
    seed(directory)
    included = bill('a', '2026-09-30T16:00:00+00:00')
    with connection(directory / NAME) as db:
        for record in (included, bill('before', '2026-09-30T15:59:59+00:00'),
                       bill('end', '2026-10-01T16:00:00+00:00'),
                       bill('b', '2026-10-01T00:00:00+00:00', 'other', currency='CNY'),
                       bill('c', '2026-10-01T01:00:00+00:00', amount=None, source='unknown')):
            write_request(db, record)
    with sqlite3.connect(directory / '2026-09.sqlite3') as db:
        db.execute('CREATE TABLE requests(id TEXT PRIMARY KEY,started TEXT,record TEXT)')
        db.execute('INSERT INTO requests VALUES (?,?,?)', ('a', included['started_at'], json.dumps(included)))
    result = usage_summary(tmp_path, {}, NOW, calendar_window('today', NOW))
    assert result['requests_today'] == 3 and result['tokens_today'] == 360
    assert result['unknown_cost_requests'] == 1 and len(result['groups']) == 2
    assert result['currencies']['USD']['rate_estimate'] == '0.002'
    assert result['currencies']['CNY']['rate_estimate'] == '0.002'
    assert sum(row['requests'] for row in result['groups']) == 3


def test_thirty_day_scope_includes_january_during_short_february(tmp_path):
    directory = tmp_path / 'llm-costs'
    seed(directory)
    now = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
    with connection(directory / NAME) as db:
        write_request(db, bill('jan', '2026-01-31T12:00:00+00:00'))
    result = dashboard(tmp_path, {}, stats_period='30d', now=now)
    assert result['window']['date_from'] == '2026-01-31'
    assert result['usage']['tokens'] == 120 and result['usage']['tokens_today'] == 0
    assert result['saved_count'] == 0 and result['timings']['count'] == 0


def test_measurement_cap_and_future_schema_are_visible(tmp_path, monkeypatch):
    directory = tmp_path / 'llm-costs'
    seed(directory)
    window = calendar_window('today', NOW)
    with connection(directory / NAME) as db:
        for name in ('one', 'two'):
            task(db, name, window['utc_start'])
            measurement(db, name, 1000)
    monkeypatch.setattr('core.activity.queries.MAX_MEASUREMENTS', 1)
    assert timing_summary(tmp_path, {}, window)['limited']
    with connection(directory / NAME) as db:
        db.execute('PRAGMA user_version=99')
    assert timing_summary(tmp_path, {}, window)['state'] == 'unavailable'


def test_large_metadata_keeps_statistics_within_owned_pipe_budget():
    row = {'provider': 'Synthetic' * 8000}
    result = {'timings': {'tasks': [row] * 50, 'groups': [row] * 32, 'count': 123},
              'usage': {'groups': [row] * 128, 'tokens': 1234}}
    bound_summary(result)
    assert len(json.dumps(result, ensure_ascii=False).encode('utf-8')) <= SUMMARY_BYTES
    assert result['timings']['limited'] and result['usage']['limited']
    assert result['timings']['count'] == 123 and result['usage']['tokens'] == 1234


@pytest.mark.parametrize('availability,outcome,value', [
    ('observed', 'failed', 999000), ('not_applicable', 'skipped', None), ('lost', 'failed', None),
])
def test_distribution_statistics_keep_latest_outcome_separate(tmp_path, availability, outcome, value):
    directory = tmp_path / 'llm-costs'
    seed(directory)
    window = calendar_window('today', NOW)
    start = datetime.fromisoformat(window['utc_start'])
    with connection(directory / NAME) as db:
        # Reverse identifier order and include an outlier: recency cannot use ID or duration order.
        for index, (identifier, duration) in enumerate((('z-old', 100000), ('x-mid', 2000),
                                                       ('b-mid', 3000), ('a-new', 1000))):
            task(db, identifier, (start + timedelta(minutes=index)).isoformat(timespec='microseconds'))
            measurement(db, identifier, duration)
        task(db, 'latest', (start + timedelta(minutes=5)).isoformat(timespec='microseconds'))
        measurement(db, 'latest', value, availability, outcome=outcome)
    groups = timing_summary(tmp_path, {}, window)['groups']
    group = next(row for row in groups if row['metric'] == 'microphone.wake')
    assert (group['count'], group['median'], group['mean'], group['max'], group['p95']) == (4, 2.5, 26.5, 100, 100)
    assert group['recent']['task_id'] == 'latest'
    assert group['recent']['outcome'] == outcome and group['recent']['availability'] == availability
    assert group['recent']['value_ms'] == (999 if value is not None else None)
    empty = next(row for row in groups if row['metric'] == 'llm.prepare')
    assert empty['count'] == 0 and all(empty[key] is None for key in ('mean', 'median', 'max', 'p95', 'recent'))


def test_zero_duration_is_a_valid_sample_and_latest_uses_task_time(tmp_path):
    directory = tmp_path / 'llm-costs'
    seed(directory)
    window = calendar_window('today', NOW)
    with connection(directory / NAME) as db:
        task(db, 'z-old', window['utc_start'])
        measurement(db, 'z-old', 1000)
        task(db, 'a-new', '2026-10-01T00:00:00.000000+00:00')
        measurement(db, 'a-new', 0)
    group = next(row for row in timing_summary(tmp_path, {}, window)['groups'] if row['metric'] == 'microphone.wake')
    assert group['count'] == 2 and group['mean'] == group['median'] == .5 and group['max'] == 1
    assert group['recent']['value_ms'] == 0 and group['recent']['task_id'] == 'a-new'
