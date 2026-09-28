"""Reconstruct saved stages conservatively and join bounded, read-only cost metadata."""

from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re
import sqlite3

from core.llm_accounting.config import load_cost_config
from core.llm_accounting.ledger import month_path


TASK = re.compile(r'^Task: `([^`\n]*)` · Outcome: `(completed|fallback|cancelled)`$', re.MULTILINE)
PRESET = re.compile(r'^Preset: `([^`\n]*)` · Request: `([^`\n]*)`$', re.MULTILINE)
FIELDS = {'Original transcription': 'original', 'Action input': 'input', 'Action output': 'output',
          'System prompt': 'prompt', 'Sent caret reference': 'context'}
FIELD = re.compile(r'^(Original transcription|Action input|Action output|System prompt|Sent caret reference):\n\n',
                   re.MULTILINE)


def parse_record(text, *, truncated=False):
    """The Markdown writer deduplicates some stages; absence alone proves nothing."""
    body = re.sub(r'^### [0-9]{2}:[0-9]{2}:[0-9]{2}\n+', '', text.replace('\r\n', '\n'), count=1)
    result = dict.fromkeys(('original', 'input', 'output', 'prompt', 'context', 'preset', 'request', 'outcome'))
    task = TASK.search(body)
    preset = PRESET.search(body)
    fields = list(FIELD.finditer(body))
    audio = re.search(r'^\[Audio\]\(<[^\n]*>\)\s*$', body, re.MULTILINE)
    markers = [match.start() for match in (task, preset, audio, *fields) if match]
    result['final'] = body[:min(markers) if markers else len(body)].strip()
    if task:
        result['outcome'] = task[2]
    if preset:
        result['preset'], result['request'] = preset.groups()
    for match in fields:
        end = min((position for position in markers if position > match.start()), default=len(body))
        result[FIELDS[match[1]]] = body[match.end():end].strip()
    # Only complete action records emitted by the writer imply a deduplicated output.
    if (result['output'] is None and result['input'] is not None and preset
            and result['outcome'] == 'completed' and not truncated):
        result['output'] = result['final']
    return result


def request_cost(root, llm_directory, day, request_id):
    """Read an indexed request from the recording month or its immediate neighbors."""
    if not request_id or len(request_id) > 256:
        return {'state': 'unlinked'}
    try:
        config = load_cost_config(root / llm_directory)
        directory = root / config['tracking']['directory']
        moment = date.fromisoformat(day)
        month_index = moment.year * 12 + moment.month - 1
        for index in (month_index, month_index + 1, month_index - 1):
            year, month = divmod(index, 12)
            if not 1 <= year <= 9999:
                continue
            path = month_path(directory, f'{year:04d}-{month + 1:02d}')
            if not path.is_file():
                continue
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                row = db.execute('SELECT record FROM requests WHERE id=? AND length(record)<=65536 LIMIT 1',
                                 (request_id,)).fetchone()
            if row:
                return cost_view(json.loads(row[0]))
        return {'state': 'missing'}
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
        return {'state': 'unavailable'}


def cost_view(record):
    """Expose display fields only, never raw rate snapshots or endpoint metadata."""
    result = {'state': 'found'}
    for key in ('provider', 'model', 'status'):
        value = record.get(key)
        result[key] = value[:200] if isinstance(value, str) else None
    elapsed = record.get('elapsed_ms')
    result['elapsed_ms'] = elapsed if type(elapsed) in (int, float) and 0 <= elapsed < 1e12 else None
    cost = record.get('accounting') or {}
    source = cost.get('cost_source')
    result['source'] = source if source in ('provider_reported', 'rate_estimate', 'token_estimate',
                                           'possible_cost', 'not_sent') else 'unknown'
    currency = cost.get('currency')
    result['currency'] = currency if isinstance(currency, str) and re.fullmatch('[A-Z]{3}', currency) else None
    result['amount'] = None
    value = cost.get('amount')
    if isinstance(value, str) and len(value) <= 60:
        try:
            number = Decimal(value)
            if number.is_finite() and number >= 0 and abs(number.adjusted()) <= 30:
                result['amount'] = format(number, 'f')
        except InvalidOperation:
            pass
    usage_source = cost.get('usage_source')
    result['usage_source'] = usage_source if usage_source in ('provider', 'mixed_estimate', 'heuristic') else 'unknown'
    usage = cost.get('estimated_usage') if usage_source in ('mixed_estimate', 'heuristic') else cost.get('usage')
    result['usage'] = {key: value for key, value in (usage or {}).items()
                       if key in ('input_tokens', 'output_tokens', 'cached_tokens', 'cache_write_tokens', 'reasoning_tokens')
                       and type(value) is int and 0 <= value < 10**12}
    return result
