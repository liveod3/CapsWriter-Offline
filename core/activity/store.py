"""Short transactional writes and bounded, read-only compatibility queries."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from core.i18n import Notice
from .schema import DDL, METRICS, VERSION

NAME = 'activity.sqlite3'


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def directory_for(root, config):
    from core.diagnostics import storage_path
    from core.llm_accounting.config import load_cost_config
    get = config.get if isinstance(config, dict) else lambda key, default: getattr(config, key, default)
    effective = get('_activity_directory', None)
    if effective is not None:
        return Path(effective)
    costs = load_cost_config(Path(root) / get('llm_config_dir', 'LLM'))
    return storage_path(root, costs['tracking']['directory'])


def validate_legacy(record):
    """Reject unknown payload fields rather than copying arbitrary legacy content."""
    from core.llm_accounting.usage import amount
    fields = {'schema_version', 'id', 'started_at', 'finished_at', 'status', 'error_category',
              'provider', 'model', 'preset', 'endpoint_hash', 'input_token_estimate',
              'token_estimator', 'max_output_tokens', 'rate', 'elapsed_ms', 'accounting'}
    cost_fields = {'usage', 'usage_source', 'invalid_usage', 'amount', 'cost_source', 'currency',
                   'reported_cost_field', 'http_status', 'dispatch', 'estimated_usage'}
    rate_fields = {'model', 'currency', 'input', 'output', 'cached_input', 'cache_write', 'reasoning',
                   'source', 'updated', 'assumption', 'valid_until', 'reported_cost_field', 'expired'}
    try:
        if (not isinstance(record, dict) or set(record) - fields
                or type(record['schema_version']) is not int or record['schema_version'] != 1):
            raise ValueError
        if not isinstance(record['id'], str) or not 0 < len(record['id']) <= 128:
            raise ValueError
        if datetime.fromisoformat(record['started_at']).tzinfo is None:
            raise ValueError
        if record['status'] not in {'unfinished', 'completed', 'failed', 'cancelled', 'not_sent', 'skipped'}:
            raise ValueError
        cost = record['accounting']
        if not isinstance(cost, dict) or set(cost) - cost_fields:
            raise ValueError
        if cost.get('cost_source') not in {'provider_reported', 'rate_estimate', 'token_estimate',
                                          'possible_cost', 'not_sent', 'unknown'}:
            raise ValueError
        rate = record.get('rate')
        if rate is not None and (not isinstance(rate, dict) or set(rate) - rate_fields):
            raise ValueError
        if cost.get('amount') is not None and amount(cost['amount']) is None:
            raise ValueError
        for key in ('usage', 'estimated_usage'):
            values = cost.get(key, {})
            if not isinstance(values, dict) or set(values) - {
                    'input_tokens', 'output_tokens', 'total_tokens', 'cached_tokens',
                    'cache_write_tokens', 'reasoning_tokens'}:
                raise ValueError
            if any(type(value) is not int or not 0 <= value < 10**12 for value in values.values()):
                raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ValueError(Notice('activity.invalid_record')) from None


@contextmanager
def connection(path, *, readonly=False):
    path = Path(path)
    if not readonly:
        path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path.resolve().as_uri() + ('?mode=ro' if readonly else ''),
                                uri=True, timeout=2)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version > VERSION:
            raise ValueError(Notice('activity.unsupported_version'))
        if not readonly and version == 0:
            # A new unified file only: never run this against a legacy monthly database.
            db.executescript('BEGIN IMMEDIATE;\n' + DDL)
            db.executemany('INSERT OR IGNORE INTO metric_definitions VALUES (?,?,?,?,?,?)',
                           [(name, 1, 'us', start, end, 'distribution_by_outcome_no_parent_sum')
                            for name, (start, end) in METRICS.items()])
            db.execute('INSERT OR IGNORE INTO schema_migrations VALUES (?,?,?)',
                       (VERSION, utcnow(), hashlib.sha256(DDL.encode()).hexdigest()))
            db.execute(f'PRAGMA user_version={VERSION}')
            db.commit()
        with db:
            yield db


def write_request(db, record, *, enabled=True, operation_id=None, origin='native', legacy_hash=None):
    """Maintain one compatibility projection and normalized usage/cost facts atomically."""
    raw = json.dumps(record, ensure_ascii=False, allow_nan=False)
    if len(raw) > 65536:
        raise ValueError(Notice('activity.invalid_record'))
    changed = db.execute('''INSERT INTO llm_requests VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET operation_id=COALESCE(excluded.operation_id,llm_requests.operation_id),
        status=CASE WHEN excluded.accounting_enabled=1 OR llm_requests.accounting_enabled=0
                    THEN excluded.status ELSE llm_requests.status END,
        accounting_enabled=MAX(excluded.accounting_enabled,llm_requests.accounting_enabled),
        record=CASE WHEN excluded.accounting_enabled=1 OR llm_requests.accounting_enabled=0
                    THEN excluded.record ELSE llm_requests.record END
        WHERE excluded.accounting_enabled=0 OR llm_requests.accounting_enabled=0
              OR llm_requests.status='unfinished' ''',
        (record['id'], operation_id, record['started_at'], record['started_at'][:7],
         record['status'], int(enabled), raw, origin, legacy_hash))
    cost = record.get('accounting', {})
    # Statistics-only observations must not replace a separately persisted accounting result.
    if not changed.rowcount:
        return
    if not enabled:
        for category, value in record.get('usage', {}).items():
            if type(value) is int and 0 <= value < 10**12:
                db.execute('INSERT OR IGNORE INTO llm_usage VALUES (?,?,?,?,?,?)',
                           (record['id'], category, 'provider', value, int(not record.get('usage_invalid')), None))
        return
    db.execute('DELETE FROM llm_usage WHERE request_id=?', (record['id'],))
    for source, values in (('provider', cost.get('usage', {})), ('estimate', cost.get('estimated_usage', {}))):
        for category, value in values.items():
            if type(value) is int and 0 <= value < 10**12:
                db.execute('INSERT INTO llm_usage VALUES (?,?,?,?,?,?)',
                           (record['id'], category, source, value, int(not cost.get('invalid_usage', False)),
                            record.get('token_estimator') if source == 'estimate' else None))
    revision = 0 if record['status'] == 'unfinished' else 1
    db.execute('INSERT OR IGNORE INTO llm_costs VALUES (?,?,?,?,?,?,?,?)',
               (record['id'], revision, cost.get('amount'), cost.get('currency'),
                cost.get('cost_source', 'unknown'), json.dumps(record.get('rate')), 1, utcnow()))


def records_for_month(directory, month, *, limit=10000):
    """Prefer unified rows; include unimported legacy rows once, without changing either file."""
    from core.llm_accounting.ledger import month_path
    legacy = month_path(Path(directory), month)
    result, seen, skipped, limited = [], set(), 0, False
    for path, unified in ((Path(directory) / NAME, True), (legacy, False)):
        if not path.exists():
            continue
        with connection(path, readonly=True) as db:
            deadline = time.monotonic() + 2
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            query = ('SELECT id, CASE WHEN length(record)<=65536 THEN record END FROM llm_requests '
                     'WHERE accounting_enabled=1 AND month=? ORDER BY started DESC,id LIMIT ?') if unified else (
                     'SELECT id, CASE WHEN length(record)<=65536 THEN record END FROM requests '
                     'ORDER BY started DESC,id LIMIT ?')
            params = (month, limit + 1) if unified else (limit + 1,)
            for identifier, raw in db.execute(query, params):
                if identifier in seen:
                    continue
                if len(result) >= limit:
                    limited = True
                    break
                seen.add(identifier)
                try:
                    record = json.loads(raw)
                    if not isinstance(record, dict) or record.get('schema_version', 1) != 1:
                        raise ValueError
                    record.setdefault('id', identifier)
                    result.append(record)
                except (ValueError, TypeError):
                    skipped += 1
    return result, limited, skipped


def import_legacy(directory):
    """Explicit, idempotent import; conflicting identities abort, source files stay untouched."""
    directory = Path(directory)
    report = {'imported': 0, 'existing': 0}
    import re
    for path in sorted(directory.glob('????-??.sqlite3')):
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])\.sqlite3', path.name):
            continue
        with connection(path, readonly=True) as source, connection(directory / NAME) as target:
            for identifier, raw in source.execute('SELECT id,record FROM requests ORDER BY id'):
                if len(raw) > 65536:
                    raise ValueError(Notice('activity.invalid_record'))
                record = json.loads(raw)
                validate_legacy(record)
                if record.get('schema_version') != 1 or record.get('id') != identifier:
                    raise ValueError(Notice('activity.invalid_record'))
                digest = hashlib.sha256(raw.encode()).hexdigest()
                existing = target.execute('SELECT legacy_hash,record FROM llm_requests WHERE id=?',
                                          (identifier,)).fetchone()
                if existing:
                    if existing[0] != digest and json.loads(existing[1]) != record:
                        raise ValueError(Notice('activity.import_conflict'))
                    report['existing'] += 1
                    continue
                write_request(target, record, origin='legacy_v1', legacy_hash=digest)
                report['imported'] += 1
    return report
