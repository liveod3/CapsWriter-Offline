"""Exercise request observations through synthetic HTTP and native failure boundaries."""

import asyncio
import json
import logging
import socket
import ssl
from types import SimpleNamespace

import httpx
import pytest

from core.client.llm.config import Catalog, Preset, Provider
from core.client.llm.diagnostics import RequestDiagnostics, current_request, exception_chain
from core.client.llm.service import TextActionService
from core.diagnostics import DiagnosticFormatter


@pytest.fixture
def harness(monkeypatch, tmp_path):
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(json.loads(DiagnosticFormatter('client', 'run').format(record)))

    logger = logging.Logger('synthetic-diagnostics', logging.DEBUG)
    logger.addHandler(Capture())
    logger.diagnostic_path = tmp_path / 'unused.jsonl'
    logger.diagnostic_include_text = False
    logger.diagnostic_include_context = False
    logger.diagnostic_text_max_chars = 16000
    monkeypatch.setattr('core.client.logger', logger)
    provider = Provider('p', 'openai', 'https://example.invalid/v1', 'model', api_key='synthetic-key')
    preset = Preset('correct_asr', 'Correction', 'p', 'Synthetic system prompt', use_caret_context=True)
    monkeypatch.setattr('core.client.llm.service.load_catalog', lambda _: Catalog({'p': provider}, {'correct_asr': preset}))
    config = SimpleNamespace(llm_enabled=True, llm_config_dir='LLM', llm_cost_tracking=False,
                             caret_context_enabled=True)
    original_client = httpx.AsyncClient

    def install(handler):
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original_client(
            transport=httpx.MockTransport(handler), **kw))

    return SimpleNamespace(records=records, logger=logger, provider=provider, preset=preset,
                           install=install, service=lambda: TextActionService(config, tmp_path))


def terminal(harness):
    return [r for r in harness.records if r['event'] == 'llm.action_finished']


def test_success_correlates_without_content_or_accounting(harness):
    harness.install(lambda _: httpx.Response(200, json={'choices': [{'message': {'content': 'result'}}],
                                                       'usage': {'prompt_tokens': 10, 'completion_tokens': 2}},
                                            headers={'x-request-id': 'req-123', 'content-type': 'application/json'},
                                            extensions={'http_version': b'HTTP/2'}))
    result = asyncio.run(harness.service().process('private transcript', task_id='task-1'))
    rows = terminal(harness)
    assert len(rows) == 1 and rows[0]['request_id'] == result.request_id
    assert rows[0]['task_id'] == 'task-1'
    data = rows[0]['data']
    assert data['outcome'] == 'completed' and data['http_status'] == 200
    assert data['response_state'] == 'body_complete' and data['parse_state'] == 'succeeded'
    assert data['output_chars'] == 6 and data['body_bytes_observed'] == data['body_bytes_retained'] > 0
    assert data['transport_state'] == 'not_observed'
    assert data['usage_state'] == 'reported' and data['usage']['input_tokens'] == 10
    assert data['http_version']['value'] == 'HTTP/2'
    assert data['response_headers']['x-request-id']['value'] == 'req-123'
    assert data['response_headers']['retry-after']['state'] == 'absent'
    assert all(value >= 0 for value in data['stage_ms'].values())
    assert all(word not in json.dumps(harness.records) for word in ('private transcript', 'synthetic-key'))
    assert current_request.get() is None


@pytest.mark.parametrize('kind', ['dns', 'tls', 'refused'])
def test_native_causes_survive_without_message_dumps(harness, kind):
    def handle(_):
        try:
            if kind == 'dns':
                raise socket.gaierror(8, 'private transcript synthetic-key')
            if kind == 'tls':
                raise ssl.SSLCertVerificationError(1, 'private transcript synthetic-key')
            raise ConnectionRefusedError(10061, 'private transcript synthetic-key')
        except OSError as exc:
            raise httpx.ConnectError('private transcript synthetic-key') from exc

    harness.install(handle)
    result = asyncio.run(harness.service().process('private transcript', task_id='dns-task'))
    data = terminal(harness)[0]['data']
    assert result.text == 'private transcript' and not result.processed
    assert data['http_status'] is None and data['response_state'] == 'awaiting_headers'
    assert data['body_bytes_observed'] == 0 and data['parse_state'] == 'not_attempted'
    assert len(data['exception_chain']) == 2
    assert data['exception_chain'][1]['reason'] == {
        'dns': 'dns_resolution_failed', 'tls': 'tls_certificate_verification_failed',
        'refused': 'connection_refused',
    }[kind]
    assert {'dns': 'DNS', 'tls': 'certificate', 'refused': 'refused'}[kind] in result.error_message
    assert 'private transcript' not in json.dumps(harness.records)
    assert 'synthetic-key' not in json.dumps(harness.records)


@pytest.mark.parametrize('enabled,context_enabled,context', [
    (False, False, ''), (True, False, ''), (True, False, 'reference'), (True, True, 'reference'),
])
def test_unknown_provider_errors_respect_opt_ins_and_redact_credentials(harness, enabled, context_enabled, context):
    harness.logger.diagnostic_include_text = enabled
    harness.logger.diagnostic_include_context = context_enabled
    harness.install(lambda _: httpx.Response(429, json={'error': {
        'code': 'NEW_PROVIDER_CODE', 'message': 'Specific diagnostic reason; api_key=synthetic-key',
        'details': [{'reason': 'NEW_REASON', 'authorization': 'different-secret'}],
        'headers': {'x-custom-secret': 'different-secret'},
    }}, headers={'x-request-id': 'synthetic-key', 'retry-after': '15', 'set-cookie': 'different-secret'}))
    result = asyncio.run(harness.service().process('source', context=context))
    assert not result.processed and 'Rate limit or quota' in result.error_message
    data = terminal(harness)[0]['data']
    assert data['error_fields']['code'] == 'unrecognized'
    assert data['response_headers']['x-request-id']['state'] == 'filtered'
    assert data['response_headers']['retry-after']['value'] == '15'
    expected = enabled and (not context or context_enabled)
    excerpts = [r for r in harness.records if r['event'] == 'llm.error_detail']
    assert bool(excerpts) == expected
    if expected:
        assert 'NEW_PROVIDER_CODE' in str(excerpts)
        assert 'Specific diagnostic reason' in str(excerpts)
    else:
        assert data['error_detail_state'] == ('context_disabled' if enabled else 'disabled')
    serialized = json.dumps(harness.records)
    assert 'synthetic-key' not in serialized and 'different-secret' not in serialized


@pytest.mark.parametrize('status,code,expected', [
    (402, None, 'payment'), (429, 'insufficient_balance', 'balance'),
    (429, 'insufficient_quota', 'quota'), (429, 'rate_limit_exceeded', 'rate'),
])
def test_failure_explanation_uses_evidence(harness, status, code, expected):
    harness.install(lambda _: httpx.Response(status, json={'error': {'code': code}}))
    result = asyncio.run(harness.service().process('source'))
    assert expected in result.error_message.lower()
    assert result.text == 'source'
    assert len(terminal(harness)) == 1


@pytest.mark.parametrize('kind', ['invalid_json', 'oversize', 'partial', 'empty_output', 'api_error_200'])
def test_response_failure_states_are_distinct(harness, kind):
    class Partial(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"error":'
            raise httpx.ReadError('synthetic incomplete body')

    def handle(_):
        if kind == 'invalid_json':
            return httpx.Response(502, content=b'<html>gateway failure</html>')
        if kind == 'oversize':
            return httpx.Response(400, content=b'x' * (65536 + 1))
        if kind == 'partial':
            return httpx.Response(503, stream=Partial())
        if kind == 'api_error_200':
            return httpx.Response(200, json={'error': {'code': 'insufficient_quota'}})
        return httpx.Response(200, json={'choices': []})

    harness.install(handle)
    result = asyncio.run(harness.service().process('source'))
    assert result.text == 'source' and not result.processed
    data = terminal(harness)[0]['data']
    assert data['http_status'] is not None
    if kind == 'partial':
        assert data['response_state'] == 'body_partial' and data['parse_state'] == 'not_attempted'
        assert data['body_bytes_observed'] == 9
    elif kind == 'oversize':
        assert data['body_truncated'] and data['parse_state'] == 'skipped_size'
        assert data['body_bytes_observed'] == 65537 and data['body_bytes_retained'] == 0
    else:
        assert data['response_state'] == 'body_complete'
        assert data['parse_state'] == ('invalid' if kind == 'invalid_json' else 'succeeded')


def test_trace_uses_only_supported_names_and_never_info_payload(harness):
    async def handle(request):
        trace = request.extensions['trace']
        for _ in range(20):
            await trace('connection.connect_tcp.started', {'host': 'synthetic-key'})
            await trace('connection.connect_tcp.complete', {'return_value': 'private source'})
        await trace('new.private_source.started', {})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'done'}}]})

    harness.install(handle)
    asyncio.run(harness.service().process('private source'))
    data = terminal(harness)[0]['data']
    assert data['transport_state'] == 'observed'
    assert data['transport_events_omitted'] == 8
    assert data['transport']['connection.connect_tcp']['elapsed_ms'] >= 0
    assert len([r for r in harness.records if r['event'] == 'llm.transport_stage']) == 32
    assert all(v not in json.dumps(harness.records) for v in ('private source', 'synthetic-key'))


@pytest.mark.parametrize('outcome', ['timeout', 'cancelled'])
def test_interrupted_body_has_one_terminal_outcome(harness, outcome):
    async def run():
        entered = asyncio.Event()
        closed = asyncio.Event()

        class Waiting(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{'
                entered.set()
                await asyncio.Event().wait()

            async def aclose(self):
                closed.set()

        harness.install(lambda _: httpx.Response(200, stream=Waiting()))
        if outcome == 'timeout':
            object.__setattr__(harness.provider, 'timeout', 0.1)
        service = harness.service()
        task = asyncio.create_task(service.process('source', task_id='task'))
        await entered.wait()
        if outcome == 'cancelled':
            service.cancel()
        result = await task
        assert closed.is_set() and not service._active
        assert result.cancelled if outcome == 'cancelled' else result.error == 'TimeoutError'
        rows = terminal(harness)
        assert len(rows) == 1
        assert rows[0]['data']['response_state'] == 'body_partial'
        assert rows[0]['data']['last_stage'] == 'body_read'
        assert rows[0]['data']['outcome'] == ('cancelled' if outcome == 'cancelled' else 'failed')

    asyncio.run(run())


def test_missing_key_is_not_a_dispatched_request(harness):
    object.__setattr__(harness.provider, 'api_key', '')
    harness.install(lambda _: pytest.fail('must not call transport'))
    result = asyncio.run(harness.service().process('source'))
    data = terminal(harness)[0]['data']
    assert result.error == 'MissingAPIKeyError'
    assert data['outcome'] == 'not_sent' and not data['dispatch_attempted']
    assert data['response_state'] == 'not_started' and data['last_stage'] == 'credentials'


def test_exception_cycle_and_snapshot_identity_are_bounded(harness):
    one, two = OSError(5, 'secret'), RuntimeError('secret')
    one.__cause__, two.__cause__ = two, one
    chain = exception_chain(one)
    assert len(chain['exception_chain']) == 2 and chain['exception_chain_truncated']
    snapshots = []
    for _ in range(2):
        diagnostic = RequestDiagnostics(harness.logger, 'request')
        diagnostic.configure(harness.provider, harness.preset, 'transcript', '')
        snapshots.append(diagnostic.identity['config_revision'])
    assert snapshots[0] == snapshots[1]
    object.__setattr__(harness.provider, 'api_key', 'changed-key')
    diagnostic.configure(harness.provider, harness.preset, 'transcript', '')
    assert diagnostic.identity['config_revision'] != snapshots[0]


def test_error_content_bounds_and_reader_permissions(harness, tmp_path, capsys):
    from scripts.read_logs import main

    harness.logger.diagnostic_include_text = True
    harness.logger.diagnostic_text_max_chars = 80
    harness.install(lambda _: httpx.Response(400, json={'error': {'message': 'private error ' * 100}}))
    asyncio.run(harness.service().process('source'))
    row = next(r for r in harness.records if r['event'] == 'llm.error_detail')
    assert row['content']['error_detail']['truncated']
    assert len(row['content']['error_detail']['text']) == 80
    path = tmp_path / 'synthetic.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in harness.records), encoding='utf-8')
    main([str(path), '--json'])
    assert 'private error' not in capsys.readouterr().out
    main([str(path), '--content', '--request', row['request_id']])
    assert 'private error' in capsys.readouterr().out


def test_concurrent_requests_keep_observations_isolated(harness):
    async def handle(request):
        await asyncio.sleep(0)
        transcript = json.loads(json.loads(request.content)['messages'][1]['content'])['transcript']
        return httpx.Response(200 if transcript == 'one' else 403,
                              json={'choices': [{'message': {'content': 'ok'}}]} if transcript == 'one'
                              else {'error': {'code': 'permission_denied'}})

    async def run():
        harness.install(handle)
        service = harness.service()
        await asyncio.gather(service.process('one', task_id='a'), service.process('two', task_id='b'))

    asyncio.run(run())
    rows = terminal(harness)
    assert len(rows) == 2 and len({r['request_id'] for r in rows}) == 2
    assert {r['task_id']: r['data']['http_status'] for r in rows} == {'a': 200, 'b': 403}


def test_diagnostic_sink_failure_cannot_replace_success(harness, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('synthetic sink failure')

    monkeypatch.setattr('core.client.llm.diagnostics.diagnostic_event', unavailable)
    harness.install(lambda _: httpx.Response(200, json={'choices': [{'message': {'content': 'done'}}]}))
    result = asyncio.run(harness.service().process('source'))
    assert result.processed and result.text == 'done'


def test_environment_key_redacted_after_whitespace_normalization(harness, monkeypatch):
    harness.logger.diagnostic_include_text = True
    object.__setattr__(harness.provider, 'api_key_env', 'SYNTHETIC_KEY_ENV')
    monkeypatch.setenv('SYNTHETIC_KEY_ENV', '  environment-secret  ')
    harness.install(lambda _: httpx.Response(400, json={'error': {'message': 'environment-secret synthetic-key'}}))
    asyncio.run(harness.service().process('source'))
    assert 'environment-secret' not in json.dumps(harness.records)
    assert 'synthetic-key' not in json.dumps(harness.records)


def test_disabled_and_unselected_actions_do_not_dispatch(harness):
    harness.install(lambda _: pytest.fail('must not call transport'))
    service = harness.service()
    service.config.llm_enabled = False
    asyncio.run(service.process('source'))
    assert not harness.records
    service.config.llm_enabled = True
    service.config.llm_default_preset = None
    asyncio.run(service.process('source'))
    rows = terminal(harness)
    assert len(rows) == 1 and rows[0]['data']['outcome'] == 'skipped'


def test_configuration_failure_does_not_dump_unresolved_credentials(harness, monkeypatch):
    def invalid(_):
        raise ValueError('unknown-configuration-secret')

    monkeypatch.setattr('core.client.llm.service.load_catalog', invalid)
    result = asyncio.run(harness.service().process('source'))
    assert not result.processed
    data = terminal(harness)[0]['data']
    assert data['last_stage'] == 'configuration' and not data['dispatch_attempted']
    assert data['failure_category'] == 'configuration_error'
    assert 'unknown-configuration-secret' not in json.dumps(harness.records)


def test_error_tree_limit_is_visible_and_keeps_useful_unknown_code(harness):
    harness.logger.diagnostic_include_text = True
    harness.install(lambda _: httpx.Response(400, json={'error': {
        'code': 'NEW_CODE', 'details': [{'reason': 'new reason'} for _ in range(50)],
    }}))
    asyncio.run(harness.service().process('source'))
    data = terminal(harness)[0]['data']
    assert data['error_fields']['details'] == 'present'
    assert data['error_details_count'] == 50 and data['error_structure_truncated']
    assert 'NEW_CODE' in str([r for r in harness.records if r['event'] == 'llm.error_detail'])


def test_non_json_error_sanitizes_header_lines(harness):
    harness.logger.diagnostic_include_text = True
    harness.install(lambda _: httpx.Response(502, text='Gateway failed\nAuthorization: Basic unknown-secret\nSet-Cookie: session=private-cookie\nhttps://example.invalid/?key=private-key'))
    asyncio.run(harness.service().process('source'))
    rows = [r for r in harness.records if r['event'] == 'llm.error_detail']
    assert 'Gateway failed' in str(rows)
    assert not any(secret in str(rows) for secret in ('unknown-secret', 'private-cookie', 'private-key'))


def test_oversize_excerpt_does_not_turn_into_an_unrelated_captured_message(harness):
    harness.logger.diagnostic_include_text = True
    # A JSON error carried by HTTP 200 uses the existing 2 MiB body limit.
    harness.install(lambda _: httpx.Response(200, json={'error': {'message': 'x' * 65000, 'extra': 'y' * 65000}}))
    asyncio.run(harness.service().process('source'))
    data = terminal(harness)[0]['data']
    assert data['error_detail_state'] == 'omitted_size'
    assert data['error_detail_source'] == 'provider_error'
    assert not any(r['event'] == 'llm.error_detail' for r in harness.records)
