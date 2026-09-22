"""Synthetic accounting boundaries; no credentials, provider calls, audio or UI."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from core.client.llm.config import Catalog, Preset, Provider
from core.client.llm.provider import HTTPTextProvider
from core.client.llm.service import TextActionService
from core.llm_accounting.config import load_cost_config
from core.llm_accounting.ledger import CostLedger, accounting, priced, read_month
from core.llm_accounting.usage import UsageObservation, observation


ROOT = Path(__file__).resolve().parents[2]
GOOGLE = 'https://generativelanguage.googleapis.com/v1beta/openai'
PROVIDER = Provider('gemini', 'openai', GOOGLE, 'gemini-3.5-flash-lite')
NOW = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)
MESSAGES = [{'role': 'user', 'content': 'PRIVATE_TEXT_SENTINEL'}]


@pytest.fixture
def ledger(tmp_path):
    directory = tmp_path / 'LLM'
    directory.mkdir()
    (directory / 'costs.template.toml').write_bytes((ROOT / 'LLM/costs.template.toml').read_bytes())
    return CostLedger(tmp_path, directory)


def prepare(ledger, identifier='request', provider=PROVIDER, now=NOW):
    ticket, warning = ledger.prepare(provider, MESSAGES, 2048, identifier, 'correct_asr', now)
    assert not warning
    return ticket


def test_rates_cache_reasoning_and_decimal_precision():
    rate = {'input': '1', 'output': '4', 'cached_input': '.1', 'cache_write': '2',
            'reasoning': '6'}
    usage = {'input_tokens': 100, 'output_tokens': 50, 'cached_tokens': 20,
             'cache_write_tokens': 10, 'reasoning_tokens': 15}
    assert Decimal(priced(usage, rate)) == Decimal('0.000322')
    assert priced({'input_tokens': 1, 'output_tokens': 0}, rate) == '0.000001'
    assert priced(usage, {k: v for k, v in rate.items() if k != 'cache_write'}) is None
    assert priced({**usage, 'cached_tokens': 200}, rate) is None


@pytest.mark.parametrize('cost', [0, '0.000000123456789', 0.5])
def test_reported_money_wins_over_estimate_even_on_failure(ledger, cost):
    provider = Provider('router', 'openai', 'https://openrouter.ai/api/v1', 'model')
    ticket = prepare(ledger, provider=provider)
    observed = UsageObservation(sent=True)
    observed.capture({'usage': {'cost': cost, 'cost_details': {'upstream_inference_cost': 500},
                                'prompt_tokens': 100, 'completion_tokens': 20}}, provider)
    result, totals, _ = ledger.finish(ticket, observed, 'failed', 'incomplete_output', 10)
    assert result['cost_source'] == 'provider_reported'
    assert Decimal(result['amount']) == Decimal(str(cost))
    assert totals['statuses'] == {'failed': 1}
    assert totals['currencies']['USD']['provider_reported'] == str(Decimal(str(cost)))


@pytest.mark.parametrize('bad', [True, -1, 'NaN', 'Infinity', {}, 'secret', 1e30])
def test_untrusted_money_and_tokens_are_not_accepted(bad):
    provider = Provider('r', 'openai', 'https://openrouter.ai/api/v1', 'model')
    observed = UsageObservation()
    observed.capture({'usage': {'cost': bad, 'prompt_tokens': bad}}, provider)
    assert observed.reported_cost is None
    assert observed.invalid_usage
    assert not observed.usage


def test_custom_endpoint_requires_explicit_price_and_currency_mapping(ledger):
    provider = Provider('local', 'openai', 'https://proxy.invalid/v1?key=PRIVATE_KEY', PROVIDER.model)
    ticket = prepare(ledger, provider=provider)
    observed = UsageObservation(sent=True)
    observed.capture({'usage': {'cost': 2}}, provider)
    cost, _, _ = ledger.finish(ticket, observed, 'cancelled', None, 10)
    assert cost['amount'] is None
    assert cost['estimated_usage']['output_tokens'] == 2048
    raw = json.dumps(read_month(ledger.base_dir / 'llm-costs', '2026-09', details=True))
    assert 'PRIVATE_TEXT_SENTINEL' not in raw and 'PRIVATE_KEY' not in raw
    assert 'proxy.invalid' not in raw
    observed.capture({'billing': {'total': '0.12'}}, provider,
                     {'reported_cost_field': 'billing.total', 'currency': 'CNY'})
    assert observed.reported_cost == '0.12' and observed.cost_currency == 'CNY'


def test_ollama_is_not_assumed_free():
    observed = UsageObservation()
    observed.capture({'prompt_eval_count': 10, 'eval_count': 5},
                     Provider('local', 'ollama', 'http://localhost:11434', 'model'))
    assert observed.usage == {'input_tokens': 10, 'output_tokens': 5}
    assert observed.reported_cost is None


def test_token_provenance_is_independent_of_money_and_missing_prices(ledger):
    ticket = prepare(ledger)
    observed = UsageObservation(sent=True, reported_cost='0.1', cost_currency='USD')
    cost = accounting(ticket[1], observed, 'failed')
    assert cost['cost_source'] == 'provider_reported' and cost['usage_source'] == 'heuristic'
    assert cost['estimated_usage']['output_tokens'] == 2048
    ticket[1]['rate'] = None
    cost = accounting(ticket[1], UsageObservation(usage={'input_tokens': 10, 'output_tokens': 5}),
                      'completed')
    assert cost['cost_source'] == 'unknown' and cost['usage_source'] == 'provider'
    assert 'estimated_usage' not in cost


def test_native_and_compatible_gemini_thoughts_are_counted_once():
    native = UsageObservation()
    native.capture({'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 20,
                                     'thoughtsTokenCount': 30, 'cachedContentTokenCount': 10}}, PROVIDER)
    assert native.usage['output_tokens'] == 50
    for output in (20, 50):
        compatible = UsageObservation()
        compatible.capture({'usage': {'prompt_tokens': 100, 'completion_tokens': output,
                                     'reasoning_tokens': 30, 'total_tokens': 150}}, PROVIDER)
        assert compatible.usage['output_tokens'] == 50
        assert not compatible.invalid_usage


def test_rate_snapshot_reload_expiry_and_month_rollover(ledger):
    first = prepare(ledger, 'first')
    template = (ledger.directory / 'costs.template.toml').read_text(encoding='utf-8')
    (ledger.directory / 'costs.toml').write_text(template.replace('"0.30"', '"0.60"'), encoding='utf-8')
    second = prepare(ledger, 'second', now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    usage = UsageObservation(sent=True, usage={'input_tokens': 1000000, 'output_tokens': 0})
    assert ledger.finish(first, usage, 'completed', None, 1)[0]['amount'] == '0.30'
    assert ledger.finish(second, usage, 'completed', None, 1)[0]['amount'] == '0.60'
    assert read_month(ledger.base_dir / 'llm-costs', '2026-09')['requests'] == 1
    assert read_month(ledger.base_dir / 'llm-costs', '2026-10')['requests'] == 1
    rate = {**first[1]['rate'], 'expired': True}
    assert priced(usage.usage, rate) is None


def test_pending_crash_record_and_failed_scenario_are_explicit(ledger):
    ticket = prepare(ledger)
    report = read_month(ledger.base_dir / 'llm-costs', '2026-09', details=True)
    pending = report['records'][0]
    assert pending['status'] == 'unfinished' and pending['finished_at'] is None
    assert pending['accounting']['dispatch'] == 'unknown'
    assert pending['accounting']['cost_source'] == 'possible_cost'
    assert pending['accounting']['estimated_usage']['output_tokens'] == 2048
    result, totals, _ = ledger.finish(ticket, UsageObservation(sent=True), 'cancelled', None, 10)
    assert result['cost_source'] == 'possible_cost' and Decimal(result['amount']) > 0
    assert totals['statuses'] == {'cancelled': 1}
    assert totals['currencies']['USD']['provider_reported'] == '0'


def test_missing_key_has_confirmed_no_dispatch_and_no_charge(ledger):
    cost, totals, _ = ledger.finish(prepare(ledger), UsageObservation(), 'not_sent', 'missing_api_key', 1)
    assert cost['amount'] == '0' and cost['cost_source'] == 'not_sent'
    assert totals['currencies'] == {} and totals['unknown_cost_requests'] == 0


def test_alerts_are_transactional_deduplicated_persistent_and_currency_specific(ledger):
    def charge(i):
        other = CostLedger(ledger.base_dir, ledger.directory)
        ticket = prepare(other, str(i))
        return other.finish(ticket, UsageObservation(sent=True, reported_cost='0.6', cost_currency='USD'),
                            'completed', None, 1)[2]
    with ThreadPoolExecutor(max_workers=4) as pool:
        alerts = [alert for group in pool.map(charge, range(4)) for alert in group]
    assert len(alerts) == 1 and alerts[0]['threshold'] == '1'
    assert charge(5) == []
    ticket = prepare(ledger, 'yuan')
    _, totals, alerts = ledger.finish(ticket, UsageObservation(sent=True, reported_cost='100', cost_currency='CNY'),
                                      'completed', None, 1)
    assert alerts == [] and totals['currencies']['CNY']['planning_total'] == '100'
    next_month = prepare(ledger, 'oct', now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert len(ledger.finish(next_month, UsageObservation(reported_cost='1', cost_currency='USD'),
                             'completed', None, 1)[2]) == 1


def test_disabled_alerts_and_tracking_and_invalid_reload(ledger):
    path = ledger.directory / 'costs.toml'
    template = (ledger.directory / 'costs.template.toml').read_text(encoding='utf-8')
    path.write_text(template.replace('alerts_enabled = true', 'alerts_enabled = false'), encoding='utf-8')
    ticket = prepare(ledger)
    assert ledger.finish(ticket, UsageObservation(reported_cost='10', cost_currency='USD'),
                         'completed', None, 1)[2] == []
    path.write_text('[broken', encoding='utf-8')
    fallback, warning = ledger.prepare(PROVIDER, MESSAGES, 10, 'fallback', 'correct_asr', NOW)
    assert warning and fallback[2]['tracking']['alerts_enabled'] is False
    path.write_text('[tracking]\nenabled = false', encoding='utf-8')
    assert ledger.prepare(PROVIDER, MESSAGES, 10, 'disabled', 'correct_asr', NOW) == (None, False)


@pytest.mark.parametrize('change', [
    ('input = "0.30"', 'input = -1'), ('currency = "USD"', 'currency = "secret"'),
    ('updated = "2026-09-22"', 'updated = "bad"'),
    ('alerts_enabled = true', 'alerts_enabled = 1'),
    ('USD = [1, 5, 10]', 'USD = [true]'),
    ('input = "0.30"', 'input = "0.30"\napi_key = "PRIVATE_KEY"'),
])
def test_invalid_rate_configuration(ledger, change):
    template = (ledger.directory / 'costs.template.toml').read_text(encoding='utf-8')
    (ledger.directory / 'costs.toml').write_text(template.replace(*change), encoding='utf-8')
    with pytest.raises(ValueError):
        load_cost_config(ledger.directory)


@pytest.mark.parametrize('finish_reason,status', [('stop', 'completed'), ('length', 'failed')])
def test_real_transport_captures_usage_before_output_validation(ledger, monkeypatch, finish_reason, status):
    client_type = httpx.AsyncClient
    body = {'usage': {'prompt_tokens': 100, 'completion_tokens': 50,
                     'prompt_tokens_details': {'cached_tokens': 20},
                     'completion_tokens_details': {'reasoning_tokens': 30}},
            'choices': [{'finish_reason': finish_reason, 'message': {'content': 'PRIVATE_OUTPUT'}}]}
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client_type(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)), **kw))
    monkeypatch.setattr('core.client.llm.service.load_catalog', lambda _: Catalog(
        {'gemini': PROVIDER}, {'correct_asr': Preset('correct_asr', 'Correct', 'gemini', 'PRIVATE_PROMPT')}))
    callback = Mock()
    service = TextActionService(SimpleNamespace(llm_enabled=True), ledger.base_dir, status_callback=callback)
    result = asyncio.run(service.process('PRIVATE_INPUT', context='PRIVATE_CONTEXT'))
    report = read_month(ledger.base_dir / 'llm-costs', datetime.now().strftime('%Y-%m'), details=True)
    record = report['records'][0]
    assert record['status'] == status
    assert record['accounting']['cost_source'] == 'rate_estimate'
    assert record['accounting']['usage']['reasoning_tokens'] == 30
    assert record['accounting']['http_status'] == 200
    assert 'PRIVATE_' not in json.dumps(report)
    assert result.processed == (status == 'completed')
    assert callback.call_count == 1
    assert callback.call_args.kwargs == {'duration_ms': 2500}


@pytest.mark.parametrize('mode', ['cancel', 'timeout', 'error', 'missing_key'])
def test_service_terminal_failures_and_fallbacks(ledger, monkeypatch, mode):
    from core.client.llm.provider import MissingAPIKeyError

    entered = None
    provider = Provider('gemini', 'openai', GOOGLE, PROVIDER.model, timeout=.03)
    monkeypatch.setattr('core.client.llm.service.load_catalog', lambda _: Catalog(
        {'gemini': provider}, {'correct_asr': Preset('correct_asr', 'Correct', 'gemini', 'system')}))

    async def complete(*_):
        entered.set()
        if mode == 'missing_key':
            observation.get()[0].sent = False
            raise MissingAPIKeyError(from_environment=True)
        if mode == 'error':
            raise httpx.ConnectError('PRIVATE_ERROR')
        await asyncio.Event().wait()

    async def run():
        nonlocal entered
        entered = asyncio.Event()
        service = TextActionService(SimpleNamespace(llm_enabled=True), ledger.base_dir,
                                    SimpleNamespace(complete=complete))
        task = asyncio.create_task(service.process('PRIVATE_INPUT'))
        await entered.wait()
        if mode == 'cancel':
            service.cancel()
        return await task

    result = asyncio.run(run())
    assert result.text == 'PRIVATE_INPUT' and not result.processed
    report = read_month(ledger.base_dir / 'llm-costs', datetime.now().strftime('%Y-%m'), details=True)
    record = report['records'][0]
    assert record['status'] == {'cancel': 'cancelled', 'timeout': 'failed', 'error': 'failed',
                                 'missing_key': 'not_sent'}[mode]
    assert record['accounting']['cost_source'] == ('not_sent' if mode == 'missing_key' else 'possible_cost')
    assert 'PRIVATE_' not in json.dumps(report)


def test_write_failure_and_disabled_accounting_do_not_break_text(ledger, monkeypatch):
    monkeypatch.setattr('core.client.llm.service.load_catalog', lambda _: Catalog(
        {'gemini': PROVIDER}, {'correct_asr': Preset('correct_asr', 'Correct', 'gemini', 'system')}))

    async def complete(*_):
        return 'output'

    service = TextActionService(SimpleNamespace(llm_enabled=True), ledger.base_dir,
                                SimpleNamespace(complete=complete))
    monkeypatch.setattr(service.costs, 'prepare', Mock(side_effect=OSError('PRIVATE_PATH')))
    assert asyncio.run(service.process('input')).text == 'output'
    service.config.llm_cost_tracking = False
    service.costs.prepare.reset_mock()
    assert asyncio.run(service.process('input')).text == 'output'
    service.costs.prepare.assert_not_called()
    assert not (ledger.base_dir / 'llm-costs').exists()


def test_cli_json_and_empty_month_are_read_only(ledger, capsys):
    from scripts.llm_costs import main
    prepare(ledger)
    assert main(['--directory', str(ledger.base_dir / 'llm-costs'), '--month', '2026-09',
                 '--json', '--details', '--language', 'en']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['requests'] == 1 and data['records'][0]['status'] == 'unfinished'
    assert read_month(ledger.base_dir / 'absent', '2026-01')['requests'] == 0
    assert not (ledger.base_dir / 'absent').exists()
    with pytest.raises(ValueError):
        read_month(ledger.base_dir, '../private')


def test_public_packaging_excludes_private_cost_settings_and_ledgers(tmp_path):
    from build_llm import copy_llm_configuration
    source = tmp_path / 'source'
    directory = source / 'LLM'
    directory.mkdir(parents=True)
    for name in ('providers.template.toml', 'presets.toml', 'costs.template.toml'):
        (directory / name).write_bytes((ROOT / 'LLM' / name).read_bytes())
    (directory / 'costs.toml').write_text('PRIVATE_SETTINGS', encoding='utf-8')
    (source / 'llm-costs').mkdir()
    (source / 'llm-costs/2026-09.sqlite3').write_bytes(b'PRIVATE_RECORDS')
    target = tmp_path / 'release'
    copy_llm_configuration(source, target)
    assert (target / 'LLM/costs.template.toml').is_file()
    assert not (target / 'LLM/costs.toml').exists()
    assert not (target / 'llm-costs').exists()


def test_rate_snapshot_omits_endpoint_and_invalid_totals_remain_estimates(ledger):
    ticket = prepare(ledger)
    assert 'endpoint' not in ticket[1]['rate']
    observed = UsageObservation(sent=True)
    observed.capture({'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
                                'total_tokens': 999}}, PROVIDER)
    assert observed.invalid_usage
    assert accounting(ticket[1], observed, 'completed')['cost_source'] == 'token_estimate'


def test_no_metadata_for_disabled_llm_or_absent_action(ledger, monkeypatch):
    transport = Mock()
    service = TextActionService(SimpleNamespace(llm_enabled=False), ledger.base_dir, transport)
    asyncio.run(service.process('text'))
    assert not (ledger.base_dir / 'llm-costs').exists()
    service.config.llm_enabled = True
    service.config.llm_default_preset = None
    monkeypatch.setattr('core.client.llm.service.load_catalog', lambda _: Catalog({}, {}))
    asyncio.run(service.process('text'))
    transport.complete.assert_not_called()
    assert not (ledger.base_dir / 'llm-costs').exists()


def test_finish_write_error_preserves_success(ledger, monkeypatch):
    monkeypatch.setattr('core.client.llm.service.load_catalog', lambda _: Catalog(
        {'gemini': PROVIDER}, {'correct_asr': Preset('correct_asr', 'Correct', 'gemini', 'system')}))

    async def complete(*_):
        return 'output'

    service = TextActionService(SimpleNamespace(llm_enabled=True), ledger.base_dir,
        SimpleNamespace(complete=complete))
    assert asyncio.run(service.process('text')).processed
    monkeypatch.setattr(service.costs, 'finish', Mock(side_effect=OSError('PRIVATE_PATH')))
    assert asyncio.run(service.process('text')).processed
    report = read_month(ledger.base_dir / 'llm-costs', datetime.now().strftime('%Y-%m'))
    assert report['statuses'] == {'completed': 1, 'unfinished': 1}
