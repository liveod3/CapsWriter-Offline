"""Bounded request observations, independent of accounting and transport outcomes."""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import socket
import ssl
import time

from core.logger import diagnostic_event, log_content


current_request: ContextVar[RequestDiagnostics | None] = ContextVar('llm_diagnostics', default=None)
_REVISION_KEY = os.urandom(32)
_SENSITIVE_FIELD = re.compile(r'key|token|authorization|cookie|password|secret|credential', re.I)
_TRACE_OPERATIONS = frozenset({
    'connection.connect_tcp', 'connection.start_tls',
    'http11.send_request_headers', 'http11.send_request_body', 'http11.receive_response_headers',
    'http2.send_request_headers', 'http2.send_request_body', 'http2.receive_response_headers',
})


def best_effort(function):
    """An observation failure must not replace a request result or interrupt cleanup."""
    @wraps(function)
    def observe(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except Exception:
            self.data['observation_errors'] = self.data.get('observation_errors', 0) + 1
    return observe


def exception_chain(exc):
    """Keep native error numbers and cause types, never arbitrary exception messages."""
    nodes, seen = [], set()
    while exc is not None and id(exc) not in seen and len(nodes) < 8:
        seen.add(id(exc))
        node = {'type': type(exc).__name__}
        for name in ('errno', 'winerror', 'verify_code'):
            value = getattr(exc, name, None)
            if type(value) is int:
                node[name] = value
        if isinstance(exc, socket.gaierror):
            node['reason'] = 'dns_resolution_failed'
        elif isinstance(exc, ssl.SSLCertVerificationError):
            node['reason'] = 'tls_certificate_verification_failed'
        elif isinstance(exc, ssl.SSLError):
            node['reason'] = 'tls_handshake_failed'
        elif isinstance(exc, ConnectionRefusedError):
            node['reason'] = 'connection_refused'
        nodes.append(node)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return {'exception_chain': nodes, 'exception_chain_truncated': exc is not None}


class RequestDiagnostics:
    def __init__(self, logger, request_id, task_id=None):
        self.logger = logger
        self.request_id = request_id
        self.task_id = task_id
        self.started = self.stage_started = time.monotonic()
        self.phase = 'configuration'
        self.stage_ms = {}
        self.identity = {}
        self.secrets = []
        self.private_values = []
        self.configured = False
        self.has_context = False
        self.finished = False
        self.trace_count = 0
        self.trace_started = {}
        self.trace_summary = {}
        self.trace_omitted = 0
        self.error_excerpt = None
        self.data = {
            'schema_version': 1, 'dispatch_attempted': False,
            'response_state': 'not_started', 'http_status': None,
            'body_bytes_observed': 0, 'body_bytes_retained': 0,
            'body_truncated': False, 'parse_state': 'not_attempted',
            'error_detail_state': 'not_available', 'output_chars': None,
        }
        self.emit('llm.action_started')

    @best_effort
    def emit(self, event, *, level=logging.INFO, **data):
        diagnostic_event(self.logger, event, level=level, task_id=self.task_id,
                         request_id=self.request_id, **copy.deepcopy({**self.identity, **data}))

    @best_effort
    def stage(self, phase):
        now = time.monotonic()
        self.stage_ms[self.phase] = self.stage_ms.get(self.phase, 0) + (now - self.stage_started) * 1000
        self.emit('llm.stage', level=logging.DEBUG, previous=self.phase, stage=phase,
                  previous_elapsed_ms=round((now - self.stage_started) * 1000, 3))
        self.phase, self.stage_started = phase, now

    @best_effort
    def configure(self, provider, preset, transcript, context):
        key = os.environ.get(provider.api_key_env, '') if provider.api_key_env else provider.api_key
        self.secrets = [value.strip() for value in (key, provider.api_key) if value and value.strip()]
        self.private_values = [value for value in (transcript, context, preset.system_prompt) if value]
        self.has_context = bool(context)
        self.configured = True
        # A process-scoped keyed digest distinguishes snapshots without publishing secrets or text hashes.
        snapshot = [provider.id, provider.kind, provider.base_url, provider.model, provider.timeout,
                    key, preset.id, preset.system_prompt, preset.temperature, preset.max_tokens,
                    preset.use_caret_context]
        revision = hmac.new(_REVISION_KEY, json.dumps(snapshot).encode(), hashlib.sha256).hexdigest()[:24]
        self.identity = {'provider': self.sanitize(provider.id)[:128], 'model': self.sanitize(provider.model)[:128],
                         'preset': self.sanitize(preset.id)[:128], 'config_revision': revision}
        self.data.update(input_chars=len(transcript), context_chars=len(context),
                         timeout_s=provider.timeout, provider_kind=provider.kind,
                         max_tokens=preset.max_tokens, temperature=preset.temperature,
                         environment_proxy=False, follow_redirects=False)

    def sanitize(self, value):
        # Redact before truncation so a boundary cannot expose a prefix of a configured credential.
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, '[redacted]')
            value = value.replace(json.dumps(secret)[1:-1], '[redacted]')
        value = re.sub(r'https?://[^\s<>"\']+', '[url omitted]', value, flags=re.I)
        value = re.sub(r'(?i)\bBearer\s+[^\s,"\'}]+', 'Bearer [redacted]', value)
        value = re.sub(r'(?im)\b(?:authorization|proxy-authorization|set-cookie|cookie)\s*[:=]\s*[^\r\n]+',
                       '[authentication omitted]', value)
        value = re.sub(r'''(?ix)(["']?(?:api[_-]?key|access[_-]?token|authorization|cookie|password|secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', r'\1[redacted]', value)
        return value

    def _metadata(self, value, pattern):
        if value is None:
            return {'state': 'absent'}
        if len(value) > 256 or not re.fullmatch(pattern, value):
            return {'state': 'filtered'}
        if self.sanitize(value) != value or any(part in value for part in self.private_values):
            return {'state': 'filtered'}
        return {'state': 'present', 'value': value}

    @best_effort
    def headers(self, response):
        self.data.update(response_state='headers_received', http_status=response.status_code)
        self.data['http_version'] = self._metadata(response.extensions.get('http_version', b'').decode('ascii', errors='replace') or None,
                                                   r'HTTP/(?:1\.[01]|2|3)')
        self.data['content_type'] = self._metadata(response.headers.get('content-type'),
                                                   r'[A-Za-z0-9.+/;= _-]{1,128}')
        self.data['content_length'] = self._metadata(response.headers.get('content-length'), r'\d{1,12}')
        selected = {}
        for name in ('x-request-id', 'request-id', 'x-goog-request-id', 'cf-ray'):
            selected[name] = self._metadata(response.headers.get(name), r'[A-Za-z0-9_.:-]{1,128}')
        for name in ('retry-after', 'x-ratelimit-limit-requests', 'x-ratelimit-remaining-requests',
                     'x-ratelimit-limit-tokens', 'x-ratelimit-remaining-tokens',
                     'x-ratelimit-reset-requests', 'x-ratelimit-reset-tokens'):
            selected[name] = self._metadata(response.headers.get(name), r'\d{1,12}(?:\.\d{1,6})?(?:ms|s|m|h)?')
        self.data['response_headers'] = selected
        self.emit('llm.response_headers', **self.data)

    async def trace(self, name, info):
        # Only documented event names and timing are used. info can contain headers, URLs and keys.
        operation, _, state = name.rpartition('.')
        if operation not in _TRACE_OPERATIONS or state not in {'started', 'complete', 'failed'}:
            return
        if self.trace_count >= 32:
            self.trace_omitted += 1
            return
        self.trace_count += 1
        now = time.monotonic()
        row = {'state': state}
        if state == 'started':
            self.trace_started[operation] = now
        elif operation in self.trace_started:
            row['elapsed_ms'] = round((now - self.trace_started.pop(operation)) * 1000, 3)
        self.trace_summary[operation] = row
        self.emit('llm.transport_stage', level=logging.DEBUG, operation=operation, **row)

    @best_effort
    def excerpt(self, value, source='exception_chain'):
        self.data['error_detail_source'] = source
        if not self.configured or not getattr(self.logger, 'diagnostic_include_text', False):
            self.data['error_detail_state'] = 'disabled'
            return
        if self.has_context and not getattr(self.logger, 'diagnostic_include_context', False):
            # Provider errors can echo arbitrary reference fragments; exact-string replacement is insufficient.
            self.data['error_detail_state'] = 'context_disabled'
            return
        if not getattr(self.logger, 'diagnostic_path', None) or not self.logger.isEnabledFor(logging.INFO):
            self.data['error_detail_state'] = 'sink_disabled'
            return
        if len(value) > 65536:
            self.data['error_detail_state'] = 'omitted_size'
            return
        clean = self.sanitize(value)
        self.error_excerpt = clean
        self.data['error_detail_state'] = 'captured'

    @best_effort
    def error_body(self, body):
        """Retain bounded error fields only in the explicit content channel."""
        from .errors import known_reason

        self.data['error_body_type'] = type(body).__name__
        self.data['error_fields'] = {
            field: ('absent' if not isinstance(body, dict) or field not in body else
                    'known' if known_reason(body[field]) else
                    'present' if (field == 'details' and isinstance(body[field], list)) or
                                 (field == 'message' and isinstance(body[field], str)) else
                    'numeric' if type(body[field]) is int else
                    'unrecognized' if isinstance(body[field], str) else 'invalid_type')
            for field in ('status', 'code', 'type', 'message', 'details')
        }
        self.data['error_details_count'] = len(body['details']) if isinstance(body, dict) and isinstance(body.get('details'), list) else None
        self.data['error_structure_truncated'] = False
        remaining = 128

        def clean(value, depth=0):
            nonlocal remaining
            remaining -= 1
            if remaining < 0 or depth > 4:
                self.data['error_structure_truncated'] = True
                return '[omitted_limit]'
            if isinstance(value, dict):
                result = {}
                for index, (key, item) in enumerate(value.items()):
                    if index == 20 or remaining < 0:
                        self.data['error_structure_truncated'] = True
                        break
                    if _SENSITIVE_FIELD.search(key) or key.lower() in {'headers', 'request', 'messages'}:
                        continue
                    result[self.sanitize(key)[:128]] = clean(item, depth + 1)
                return result
            if isinstance(value, list):
                if len(value) > 20:
                    self.data['error_structure_truncated'] = True
                return [clean(item, depth + 1) for item in value[:20] if remaining >= 0]
            if isinstance(value, str):
                if len(value) > 65536:
                    self.data['error_structure_truncated'] = True
                    return '[omitted_size]'
                return self.sanitize(value)
            return value

        self.excerpt(json.dumps(clean(body), ensure_ascii=False, default=str), source='provider_error')

    @best_effort
    def finish(self, outcome, category=None, fields=None, exc=None):
        if self.finished:
            return
        self.finished = True
        now = time.monotonic()
        self.stage_ms[self.phase] = self.stage_ms.get(self.phase, 0) + (now - self.stage_started) * 1000
        if exc is not None:
            self.data.update(exception_chain(exc))
            if self.data['error_detail_state'] == 'not_available' and self.configured:
                # Exception text is separate from the routine structural chain.
                parts, seen = [], set()
                cause = exc
                while cause is not None and id(cause) not in seen and len(parts) < 8:
                    seen.add(id(cause))
                    parts.append(str(cause))
                    cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
                self.excerpt('\n'.join(parts))
        if self.error_excerpt is not None:
            limit = min(65536, max(1, getattr(self.logger, 'diagnostic_text_max_chars', 16000)))
            self.data['error_detail_truncated'] = len(self.error_excerpt) > limit
            log_content(self.logger, 'llm.error_detail', task_id=self.task_id,
                        request_id=self.request_id, error_detail=self.error_excerpt)
            self.error_excerpt = None
        self.emit('llm.action_finished', level=logging.WARNING if outcome in {'failed', 'not_sent'} else logging.INFO,
                  **self.data, outcome=outcome, failure_category=category, failure=fields or {},
                  text_outcome='processed' if outcome == 'completed' else 'original_retained',
                  last_stage=self.phase, elapsed_ms=round((now - self.started) * 1000, 3),
                  stage_ms={k: round(v, 3) for k, v in self.stage_ms.items()},
                  transport_state='observed' if self.trace_count else 'not_observed',
                  transport=self.trace_summary, transport_events_omitted=self.trace_omitted)
