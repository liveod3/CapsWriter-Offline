"""Monthly transactional accounting, with independent amounts and uncertainty labels."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from core.i18n import Notice
from .config import load_cost_config, select_rate
from .usage import UsageObservation, estimate_tokens


MILLION = Decimal(1_000_000)


def priced(usage: dict, rate: dict | None) -> str | None:
    if not rate or rate.get('expired'):
        return None
    inp, out = usage.get('input_tokens'), usage.get('output_tokens')
    if inp is None or out is None:
        return None
    cached, written, reasoning = (usage.get(k, 0) for k in
                                  ('cached_tokens', 'cache_write_tokens', 'reasoning_tokens'))
    if cached + written > inp or reasoning > out:
        return None
    if (cached and 'cached_input' not in rate) or (written and 'cache_write' not in rate):
        return None
    cost = ((inp - cached - written) * Decimal(rate['input'])
            + cached * Decimal(rate.get('cached_input', '0'))
            + written * Decimal(rate.get('cache_write', '0'))
            + (out - reasoning) * Decimal(rate['output'])
            + reasoning * Decimal(rate.get('reasoning', rate['output']))) / MILLION
    return str(cost)


def accounting(record: dict, observed: UsageObservation, status: str) -> dict:
    usage = dict(observed.usage)
    rate = record['rate']
    result = {'usage': usage, 'usage_source': 'provider' if usage else 'unknown',
              'invalid_usage': observed.invalid_usage, 'amount': None,
              'cost_source': 'unknown', 'currency': rate['currency'] if rate else None,
              'reported_cost_field': observed.cost_field,
              'http_status': observed.http_status, 'dispatch': 'attempted' if observed.sent else 'not_sent'}
    # Token provenance is independent of whether a monetary total was returned.
    needs_estimate = observed.invalid_usage or any(
        name not in usage for name in ('input_tokens', 'output_tokens')
    )
    estimated = dict(usage) if not observed.invalid_usage else {}
    if needs_estimate and status != 'not_sent':
        # The requested output limit is a planning scenario for incomplete requests,
        # not an invoice or a strict upper bound on hidden generation.
        estimated.setdefault('input_tokens', record['input_token_estimate'])
        estimated.setdefault('output_tokens', (observed.output_estimate or 0)
                             if status == 'completed' else record['max_output_tokens'])
        result['estimated_usage'] = estimated
        result['usage_source'] = 'mixed_estimate' if usage else 'heuristic'
    if observed.reported_cost is not None:
        result.update(amount=observed.reported_cost, cost_source='provider_reported',
                      currency=observed.cost_currency)
        return result
    if status == 'not_sent':
        result.update(amount='0', cost_source='not_sent')
        return result
    if not observed.invalid_usage:
        cost = priced(usage, rate)
        if cost is not None:
            result.update(amount=cost, cost_source='rate_estimate')
            return result
    cost = priced(estimated, rate)
    if cost is not None:
        result.update(amount=cost, cost_source=(
            'token_estimate' if status == 'completed' else 'possible_cost'))
    return result


def summarize(records) -> dict:
    currencies = {}
    statuses = Counter()
    unknown = 0
    models = {}
    for record in records:
        statuses[record['status']] += 1
        cost = record['accounting']
        model_key = (record['provider'], record['model'])
        group = models.setdefault(model_key, {'provider': model_key[0], 'model': model_key[1],
                                              'requests': 0, 'unknown': 0, 'currencies': {}})
        group['requests'] += 1
        if cost['amount'] is None:
            unknown += 1
            group['unknown'] += 1
            continue
        if cost['cost_source'] == 'not_sent':
            continue
        currency = cost['currency']
        for totals in (currencies, group['currencies']):
            bucket = totals.setdefault(currency, {'provider_reported': Decimal(0),
                'rate_estimate': Decimal(0), 'token_estimate': Decimal(0),
                'possible_cost': Decimal(0), 'planning_total': Decimal(0)})
            value = Decimal(cost['amount'])
            bucket[cost['cost_source']] += value
            bucket['planning_total'] += value
    # Decimal strings preserve small charges and avoid binary rounding in exports.
    def strings(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {k: strings(v) for k, v in value.items()}
        if isinstance(value, list):
            return [strings(v) for v in value]
        return value
    return strings({'requests': sum(statuses.values()), 'statuses': dict(statuses),
                    'unknown_cost_requests': unknown, 'currencies': currencies,
                    'models': list(models.values())})


def month_path(directory: Path, month: str) -> Path:
    if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', month):
        raise ValueError(Notice('cost.invalid_month'))
    return directory / (month + '.sqlite3')


def read_month(directory: Path, month: str, *, details=False) -> dict:
    path = month_path(directory, month)
    if not path.exists():
        result = summarize([])
        if details:
            result['records'] = []
        return {'month': month, **result}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        records = [json.loads(row[0]) for row in db.execute('SELECT record FROM requests ORDER BY started, id')]
    result = {'month': month, **summarize(records)}
    if details:
        result['records'] = records
    return result


class CostLedger:
    def __init__(self, base_dir: Path, directory: Path):
        self.base_dir = base_dir
        self.directory = directory
        self._last_config = None

    def prepare(self, provider, messages, max_tokens, request_id, preset_id, now=None):
        warning = False
        try:
            config = load_cost_config(self.directory)
            self._last_config = config
        except (ValueError, OSError):
            warning = True
            # Preserve a complete valid snapshot if an editor temporarily writes invalid TOML.
            config = self._last_config or {
                'tracking': {'enabled': True, 'directory': 'llm-costs'}, 'rates': []}
        if not config['tracking']['enabled']:
            return None, warning
        now = now or datetime.now().astimezone()
        rate = select_rate(config, provider, now.date())
        record = {
            'schema_version': 1, 'id': request_id, 'started_at': now.isoformat(),
            'finished_at': None, 'status': 'unfinished', 'error_category': None,
            'provider': provider.id, 'model': provider.model, 'preset': preset_id,
            # Do not store endpoint URLs: custom query strings may contain credentials.
            'endpoint_hash': hashlib.sha256(provider.base_url.encode()).hexdigest()[:16],
            'input_token_estimate': sum(estimate_tokens(m['content']) + 4 for m in messages) + 2,
            'token_estimator': 'utf8_bytes_div_3_plus_message_overhead',
            'max_output_tokens': max_tokens, 'rate': rate, 'elapsed_ms': None,
        }
        record['accounting'] = accounting(record, UsageObservation(), 'unfinished')
        record['accounting']['dispatch'] = 'unknown'
        path = month_path(self.base_dir / config['tracking']['directory'], now.strftime('%Y-%m'))
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=5)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, started TEXT, record TEXT)')
            db.execute('INSERT INTO requests VALUES (?, ?, ?)',
                       (request_id, record['started_at'], json.dumps(record, ensure_ascii=False)))
        return (path, record), warning

    def finish(self, ticket, observed, status, error_category, elapsed_ms):
        path, record = ticket
        record.update(status=status, error_category=error_category, elapsed_ms=elapsed_ms,
                      finished_at=datetime.now().astimezone().isoformat())
        record['accounting'] = accounting(record, observed, status)
        with closing(sqlite3.connect(path, timeout=5)) as db, db:
            # Commit only this request; monthly aggregation belongs to the query tool.
            db.execute('UPDATE requests SET record=? WHERE id=?',
                       (json.dumps(record, ensure_ascii=False), record['id']))
        return record['accounting']
