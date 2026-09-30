"""Bounded, read-only dashboard summaries from existing local records."""

from datetime import datetime
from decimal import Decimal
import json
import re
import sqlite3

from core.diagnostics import storage_path
from core.i18n import Notice
from core.llm_accounting.config import load_cost_config
from core.llm_accounting.ledger import month_path
from core.llm_accounting.usage import amount, mapping, tokens
from .history import query_history, read_history_entry


MAX_REQUESTS = 10000
MAX_SCOPE_REQUESTS = 30000
MAX_RECORD_CHARS = 65536
COST_SOURCES = ('provider_reported', 'rate_estimate', 'token_estimate', 'possible_cost')
SUMMARY_BYTES = 1024 * 1024


def bound_summary(result):
    """Reserve pipe space even when user-defined model names or task details are huge."""
    collections = ((result['timings'], 'tasks'), (result['timings'], 'groups'), (result['usage'], 'groups'))
    while len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > SUMMARY_BYTES:
        changed = False
        for owner, key in collections:
            rows = owner.get(key, [])
            if rows:
                owner[key] = rows[:len(rows) // 2]
                owner['limited'] = True
                changed = True
        if not changed:
            break
    return result


def usage_summary(root, config, now, window=None):
    result = {'state': 'empty', 'limited': False, 'skipped': 0, 'requests_today': 0,
              'tokens_today': 0, 'known_token_requests': 0, 'unknown_token_requests': 0,
              'input_tokens': 0, 'output_tokens': 0, 'month_requests': 0,
              'unknown_cost_requests': 0, 'currencies': {},
              'requests': 0, 'tokens': 0, 'groups': [],
              'tracking_enabled': bool(config.get('llm_cost_tracking', True))}
    groups = {}
    try:
        costs = load_cost_config(root / config.get('llm_config_dir', 'LLM'))
        result['tracking_enabled'] &= costs['tracking']['enabled']
        directory = storage_path(root, config.get('_activity_directory', costs['tracking']['directory']))
        index = now.year * 12 + now.month - 1
        from core.activity.store import NAME, records_for_month
        seen = set()
        remaining = MAX_SCOPE_REQUESTS
        months = (index, index - 1, index + 1)
        if window:
            start, end = datetime.fromisoformat(window['start']), datetime.fromisoformat(window['end'])
            # Include adjacent UTC months for offset boundaries, including a 30-day February window.
            lower = start.year * 12 + start.month - 2
            upper = end.year * 12 + end.month
            months = range(upper, lower - 1, -1)
        for current in months:
            if remaining <= 0:
                result['limited'] = True
                break
            year, month = divmod(current, 12)
            month_key = f'{year:04d}-{month + 1:02d}'
            if (directory / NAME).exists() or month_path(directory, month_key).exists():
                result['state'] = 'ready'
            records, limited, skipped = records_for_month(directory, month_key, limit=min(MAX_REQUESTS, remaining))
            remaining -= len(records)
            result['limited'] |= limited
            result['skipped'] += skipped
            for record in records:
                if record.get('id') in seen:
                    continue
                seen.add(record.get('id'))
                try:
                    moment = datetime.fromisoformat(record['started_at'])
                    if moment.tzinfo is None:
                        raise ValueError
                    moment = moment.astimezone(now.tzinfo)
                    in_scope = start <= moment < end if window else (
                        (moment.year, moment.month) == (now.year, now.month))
                    if not in_scope:
                        continue
                    accounting = mapping(record['accounting'])
                except (ValueError, TypeError, KeyError, OverflowError):
                    result['skipped'] += 1
                    continue
                result['requests'] += 1
                if (moment.year, moment.month) == (now.year, now.month):
                    result['month_requests'] += 1
                source = accounting.get('cost_source')
                key = tuple(record.get(field) if isinstance(record.get(field), str) else ''
                            for field in ('provider', 'model'))
                if key not in groups and len(groups) >= 128:
                    result['limited'] = True
                    group = None
                else:
                    group = groups.setdefault(key, {'provider': key[0], 'model': key[1], 'requests': 0,
                        'tokens': 0, 'unknown_tokens': 0, 'unknown_cost': 0, 'currencies': {}})
                    group['requests'] += 1
                if window or moment.date() == now.date():
                    if moment.date() == now.date():
                        result['requests_today'] += 1
                    usage = mapping(accounting.get('usage')) if not accounting.get('invalid_usage') else {}
                    inp, out = tokens(usage.get('input_tokens')), tokens(usage.get('output_tokens'))
                    total = tokens(usage.get('total_tokens'))
                    if total is None and inp is not None and out is not None:
                        total = inp + out
                    if source == 'not_sent':
                        total = 0
                    if total is None:
                        result['unknown_token_requests'] += 1
                        if group is not None:
                            group['unknown_tokens'] += 1
                    else:
                        result['known_token_requests'] += 1
                        result['tokens'] += total
                        if moment.date() == now.date():
                            result['tokens_today'] += total
                        if group is not None:
                            group['tokens'] += total
                    result['input_tokens'] += inp or 0
                    result['output_tokens'] += out or 0
                value = amount(accounting.get('amount'))
                currency = accounting.get('currency')
                if source == 'not_sent':
                    continue
                if (source not in COST_SOURCES or value is None or not isinstance(currency, str)
                        or not re.fullmatch('[A-Z]{3}', currency)):
                    result['unknown_cost_requests'] += 1
                    if group is not None:
                        group['unknown_cost'] += 1
                    continue
                if currency not in result['currencies'] and len(result['currencies']) >= 12:
                    result['limited'] = True
                    result['unknown_cost_requests'] += 1
                    if group is not None:
                        group['unknown_cost'] += 1
                    continue
                bucket = result['currencies'].setdefault(currency, dict.fromkeys(COST_SOURCES, Decimal(0)))
                bucket[source] += value
                if group is not None:
                    cost_bucket = group['currencies'].setdefault(currency, dict.fromkeys(COST_SOURCES, Decimal(0)))
                    cost_bucket[source] += value
            if result['limited']:
                break
    except (OSError, ValueError, TypeError, sqlite3.Error):
        result['state'] = 'unavailable'
    result['currencies'] = {currency: {key: str(value) for key, value in bucket.items()}
                            for currency, bucket in result['currencies'].items()}
    for group in groups.values():
        group['currencies'] = {currency: {key: str(value) for key, value in bucket.items()}
                               for currency, bucket in group['currencies'].items()}
    result['groups'] = sorted(groups.values(), key=lambda row: (row['provider'], row['model']))
    return result


def dashboard(root, config, period='recent', *, stats_period='today', now=None):
    if period not in ('recent', 'today'):
        raise ValueError(Notice('gui.history_invalid_query'))
    from core.activity.periods import calendar_window
    window = calendar_window(stats_period, now)
    now = now or datetime.now().astimezone()
    directory = storage_path(root, config.get('transcript_dir', 'records/transcripts'))
    result = {'day': now.date().isoformat(), 'month': now.strftime('%Y-%m'),
              'updated': now.strftime('%H:%M:%S'), 'period': period, 'entries': [],
              'window': window, 'saved_count': 0,
              'history_state': 'ready', 'today_count': 0, 'history_limited': False,
              'saving_enabled': bool(config.get('save_transcripts') or config.get('save_llm_records'))}
    try:
        today = query_history(directory, date_from=result['day'], date_to=result['day'])
        recent = today if period == 'today' else query_history(directory)
        result['today_count'] = today['total']
        selected = today if stats_period == 'today' else query_history(
            directory, date_from=window['date_from'], date_to=window['date_to'])
        result['saved_count'] = selected['total']
        result['history_limited'] = any(row['limited'] or row['skipped'] for row in (today, recent, selected))
        result['entries'] = recent['entries'][:10]
    except (OSError, ValueError):
        result['history_state'] = 'unavailable'
    result['usage'] = usage_summary(root, config, now, window)
    from core.activity.queries import timing_summary
    result['timings'] = timing_summary(root, config, window)
    return bound_summary(result)


def copy_result(root, config, reference):
    directory = storage_path(root, config.get('transcript_dir', 'records/transcripts'))
    entry = read_history_entry(directory, **reference)
    if entry['truncated'] or not entry['stages']['final']:
        raise ValueError(Notice('gui.dashboard_copy_unavailable'))
    return entry['stages']['final']
