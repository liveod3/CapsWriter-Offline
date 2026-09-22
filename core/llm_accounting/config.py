"""Validate static accounting settings and exact endpoint/model rate mappings."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit

from core.i18n import Notice
from .usage import amount


def load_cost_config(directory: Path) -> dict:
    path = directory / 'costs.toml'
    if not path.exists():
        path = directory / 'costs.template.toml'
    if not path.exists():
        # Older/custom installations still record unknown costs, without invented rates.
        data = {}
    else:
        with path.open('rb') as stream:
            data = tomllib.load(stream)
    try:
        tracking = {'enabled': True, 'directory': 'llm-costs', 'show_summary': True,
                    'alerts_enabled': True, **data.get('tracking', {})}
        for key in ('enabled', 'show_summary', 'alerts_enabled'):
            if type(tracking[key]) is not bool:
                raise ValueError
        if not isinstance(tracking['directory'], str) or not tracking['directory'].strip():
            raise ValueError
        budgets = data.get('budgets', {})
        if not isinstance(budgets, dict):
            raise ValueError
        for currency, thresholds in budgets.items():
            if not re.fullmatch('[A-Z]{3}', currency) or not isinstance(thresholds, list):
                raise ValueError
            if len(thresholds) > 100 or any(amount(v) is None or amount(v) <= 0 for v in thresholds):
                raise ValueError
        rates = data.get('rates', [])
        if not isinstance(rates, list) or len(rates) > 1000:
            raise ValueError
        seen = set()
        for rate in rates:
            if set(rate) - {'endpoint', 'model', 'currency', 'input', 'output', 'cached_input',
                            'cache_write', 'reasoning', 'source', 'updated', 'assumption',
                            'valid_until', 'reported_cost_field'}:
                raise ValueError
            endpoint = urlsplit(rate['endpoint'])
            if (endpoint.scheme not in {'http', 'https'} or not endpoint.hostname
                    or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
                raise ValueError
            if not isinstance(rate['model'], str) or not rate['model'].strip():
                raise ValueError
            pair = (rate['endpoint'].rstrip('/'), rate['model'])
            if pair in seen:
                raise ValueError
            seen.add(pair)
            if not re.fullmatch('[A-Z]{3}', rate['currency']):
                raise ValueError
            for key in ('input', 'output', 'cached_input', 'cache_write', 'reasoning'):
                if key in rate:
                    value = amount(rate[key])
                    if value is None:
                        raise ValueError
                    rate[key] = str(value)
            if 'input' not in rate or 'output' not in rate:
                raise ValueError
            for key in ('source', 'assumption'):
                if not isinstance(rate[key], str) or not rate[key].strip():
                    raise ValueError
            source_url = urlsplit(rate['source'])
            if source_url.username or source_url.password or source_url.query:
                raise ValueError
            date.fromisoformat(rate['updated'])
            if 'valid_until' in rate:
                if date.fromisoformat(rate['valid_until']) < date.fromisoformat(rate['updated']):
                    raise ValueError
            if 'reported_cost_field' in rate and not re.fullmatch(
                r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*){0,4}',
                rate['reported_cost_field']
            ):
                raise ValueError
        return {'tracking': tracking, 'budgets': budgets, 'rates': rates}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError(Notice('cost.invalid_config')) from None


def select_rate(config: dict, provider, today: date) -> dict | None:
    for rate in config['rates']:
        if (rate['endpoint'].rstrip('/') == provider.base_url.rstrip('/')
                and rate['model'] == provider.model):
            snapshot = dict(rate)
            snapshot.pop('endpoint')
            snapshot['expired'] = bool(rate.get('valid_until') and
                                       today > date.fromisoformat(rate['valid_until']))
            return snapshot
    return None
