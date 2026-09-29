"""Bounded, read-only dashboard summaries from existing local records."""

from contextlib import closing
from datetime import datetime
from decimal import Decimal
import json
import re
import sqlite3
import time

from core.diagnostics import storage_path
from core.i18n import Notice
from core.llm_accounting.config import load_cost_config
from core.llm_accounting.ledger import month_path
from core.llm_accounting.usage import amount, mapping, tokens
from .history import query_history, read_history_entry


MAX_REQUESTS = 10000
MAX_RECORD_CHARS = 65536
COST_SOURCES = ('provider_reported', 'rate_estimate', 'token_estimate', 'possible_cost')


def usage_summary(root, config, now):
    result = {'state': 'empty', 'limited': False, 'skipped': 0, 'requests_today': 0,
              'tokens_today': 0, 'known_token_requests': 0, 'unknown_token_requests': 0,
              'input_tokens': 0, 'output_tokens': 0, 'month_requests': 0,
              'unknown_cost_requests': 0, 'currencies': {},
              'tracking_enabled': bool(config.get('llm_cost_tracking', True))}
    try:
        costs = load_cost_config(root / config.get('llm_config_dir', 'LLM'))
        result['tracking_enabled'] &= costs['tracking']['enabled']
        directory = storage_path(root, costs['tracking']['directory'])
        index = now.year * 12 + now.month - 1
        scanned = 0
        # Adjacent files cover offset changes near local midnight/month boundaries.
        for current in (index, index - 1, index + 1):
            year, month = divmod(current, 12)
            path = month_path(directory, f'{year:04d}-{month + 1:02d}')
            if not path.exists():
                continue
            result['state'] = 'ready'
            deadline = time.monotonic() + 2
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                rows = db.execute('SELECT CASE WHEN length(record) <= ? THEN record ELSE NULL END '
                                  'FROM requests ORDER BY started DESC LIMIT ?',
                                  (MAX_RECORD_CHARS, MAX_REQUESTS - scanned + 1))
                for (raw,) in rows:
                    if time.monotonic() > deadline:
                        result['limited'] = True
                        break
                    scanned += 1
                    if scanned > MAX_REQUESTS:
                        result['limited'] = True
                        break
                    try:
                        record = json.loads(raw)
                        moment = datetime.fromisoformat(record['started_at'])
                        if moment.tzinfo is None:
                            raise ValueError
                        moment = moment.astimezone(now.tzinfo)
                        if (moment.year, moment.month) != (now.year, now.month):
                            continue
                        accounting = mapping(record['accounting'])
                    except (ValueError, TypeError, KeyError, OverflowError):
                        result['skipped'] += 1
                        continue
                    result['month_requests'] += 1
                    source = accounting.get('cost_source')
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
                        else:
                            result['known_token_requests'] += 1
                            result['tokens_today'] += total
                        result['input_tokens'] += inp or 0
                        result['output_tokens'] += out or 0
                    value = amount(accounting.get('amount'))
                    currency = accounting.get('currency')
                    if source == 'not_sent':
                        continue
                    if (source not in COST_SOURCES or value is None or not isinstance(currency, str)
                            or not re.fullmatch('[A-Z]{3}', currency)):
                        result['unknown_cost_requests'] += 1
                        continue
                    if currency not in result['currencies'] and len(result['currencies']) >= 12:
                        result['limited'] = True
                        result['unknown_cost_requests'] += 1
                        continue
                    bucket = result['currencies'].setdefault(currency, dict.fromkeys(COST_SOURCES, Decimal(0)))
                    bucket[source] += value
            if result['limited']:
                break
    except (OSError, ValueError, TypeError, sqlite3.Error):
        result['state'] = 'unavailable'
    result['currencies'] = {currency: {key: str(value) for key, value in bucket.items()}
                            for currency, bucket in result['currencies'].items()}
    return result


def dashboard(root, config, period='recent', *, now=None):
    if period not in ('recent', 'today'):
        raise ValueError(Notice('gui.history_invalid_query'))
    now = now or datetime.now().astimezone()
    directory = storage_path(root, config.get('transcript_dir', 'records/transcripts'))
    result = {'day': now.date().isoformat(), 'month': now.strftime('%Y-%m'),
              'updated': now.strftime('%H:%M:%S'), 'period': period, 'entries': [],
              'history_state': 'ready', 'today_count': 0, 'history_limited': False,
              'saving_enabled': bool(config.get('save_transcripts') or config.get('save_llm_records'))}
    try:
        today = query_history(directory, date_from=result['day'], date_to=result['day'])
        recent = today if period == 'today' else query_history(directory)
        result['today_count'] = today['total']
        result['history_limited'] = bool(today['limited'] or today['skipped'] or recent['limited'] or recent['skipped'])
        result['entries'] = recent['entries'][:10]
    except (OSError, ValueError):
        result['history_state'] = 'unavailable'
    result['usage'] = usage_summary(root, config, now)
    return result


def copy_result(root, config, reference):
    directory = storage_path(root, config.get('transcript_dir', 'records/transcripts'))
    entry = read_history_entry(directory, **reference)
    if entry['truncated'] or not entry['stages']['final']:
        raise ValueError(Notice('gui.dashboard_copy_unavailable'))
    return entry['stages']['final']
