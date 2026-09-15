import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest

from core.client.llm.config import Provider
from core.client.llm.provider import HTTPTextProvider
from core.client.llm.errors import LLMResponseError, api_error, describe_failure


@pytest.mark.parametrize("kind", ["ollama", "openai"])
def test_transport_routes_once_and_uses_environment_key(monkeypatch, kind):
    requests = []
    client_type = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        body = (
            {"message": {"content": " result "}}
            if kind == "ollama"
            else {"choices": [{"message": {"content": " result "}}]}
        )
        return httpx.Response(200, json=body)

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(handle), **kw)
    )
    monkeypatch.setenv("TEST_CAPS_KEY", "synthetic-key")
    provider = Provider("test", kind, "https://example.invalid/v1", "model", "TEST_CAPS_KEY")
    messages = [{"role": "user", "content": "synthetic transcript"}]
    assert asyncio.run(HTTPTextProvider().complete(provider, messages, 0, 20)) == "result"
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["authorization"] == "Bearer synthetic-key"
    assert request.url.path == ("/v1/api/chat" if kind == "ollama" else "/v1/chat/completions")
    payload = json.loads(request.content)
    assert payload["messages"] == messages and payload["stream"] is False


@pytest.mark.parametrize("status,body,category,field,value", [
    (429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded for secret transcript",
                     "details": [{"retryDelay": "12.5s"}]}}, "http_error", "retry_after_s", "12.5"),
    (400, {"error": {"status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}},
     "http_error", "reason", "API_KEY_INVALID"),
    (403, {"error": {"message": "Your API key was reported as leaked. private-key"}},
     "http_error", "reason", "API_KEY_LEAKED"),
    (200, {"error": {"code": "quota_exceeded"}}, "api_error", "api_code", "QUOTA_EXCEEDED"),
    (200, {"promptFeedback": {"blockReason": "SAFETY"}}, "generation_stopped", "block_reason", "SAFETY"),
    (200, {"candidates": [{"finishReason": "RECITATION"}]},
     "generation_stopped", "finish_reason", "RECITATION"),
    (200, {"choices": [{"finish_reason": "length", "message": {"content": "partial text"}}]},
     "incomplete_output", "finish_reason", "LENGTH"),
    (200, {"choices": [{"finish_reason": "content_filter", "message": {"content": "partial text"}}]},
     "generation_stopped", "finish_reason", "CONTENT_FILTER"),
])
def test_provider_failure_reasons_preserve_structured_details_without_content(
    monkeypatch, status, body, category, field, value
):
    client_type = httpx.AsyncClient
    handle = Mock(return_value=httpx.Response(status, json=body))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(LLMResponseError) as caught:
        asyncio.run(HTTPTextProvider().complete(
            Provider("p", "openai", "https://example.invalid", "model"), [], 0, 10))
    assert caught.value.category == category
    assert caught.value.fields[field] == value
    assert all(s not in str(caught.value) + str(caught.value.fields)
               for s in ("private-key", "secret transcript", "partial text"))
    handle.assert_called_once()


@pytest.mark.parametrize("body,category", [
    ([], "invalid_response"),
    ({"choices": []}, "empty_choices"),
    ({"choices": [None]}, "invalid_response"),
    ({"choices": [{"message": {"content": None}}]}, "empty_output"),
    ({"choices": [{"message": {"refusal": "private refusal"}}]}, "generation_stopped"),
    ({"choices": [{"message": {"content": "", "tool_calls": [{}]}}]}, "generation_stopped"),
])
def test_invalid_or_unusable_response_does_not_become_success(monkeypatch, body, category):
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_type(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)), **kw))
    with pytest.raises(LLMResponseError) as caught:
        asyncio.run(HTTPTextProvider().complete(
            Provider("p", "openai", "https://example.invalid", "model"), [], 0, 10))
    assert caught.value.category == category


@pytest.mark.parametrize("exception,category", [
    (httpx.ConnectTimeout, "connect_timeout"), (httpx.ReadTimeout, "read_timeout"),
    (httpx.WriteTimeout, "write_timeout"), (httpx.PoolTimeout, "pool_timeout"),
    (httpx.ConnectError, "connection_failed"), (httpx.RemoteProtocolError, "remote_protocol_error"),
    (TimeoutError, "request_timeout"),
])
def test_network_failures_are_specific_without_logging_exception_body(exception, category):
    result = describe_failure(exception("private transcript and key"))
    assert result[0] == category
    assert "private" not in str(result)


def test_untrusted_error_fields_cannot_inject_text_into_diagnostics():
    error = api_error(403, {"error": {
        "status": "PRIVATE_TRANSCRIPT", "code": "secret-key", "message": "private text\nnew log line",
        "details": [{"reason": "private reference", "retryDelay": "secret-key"}],
    }}, retry_after="private reference")
    assert error.fields["http_status"] == 403
    assert error.fields["api_status"] == "unknown"
    assert "retry_after_s" not in error.fields
    assert all(value not in str(describe_failure(error))
               for value in ("private", "PRIVATE", "secret", "new log line"))


def test_missing_key_fails_before_creating_network_client(monkeypatch):
    client = Mock(side_effect=AssertionError("network should not start"))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.delenv("TEST_CAPS_KEY", raising=False)
    with pytest.raises(ValueError, match="environment variable"):
        asyncio.run(
            HTTPTextProvider().complete(
                Provider("x", "openai", "https://example.invalid/v1", "x", "TEST_CAPS_KEY"),
                [],
                0,
                10,
            )
        )
    client.assert_not_called()


@pytest.mark.parametrize("failure", ["timeout", "redirect", "unauthorized", "oversize", "empty"])
def test_transport_errors_are_bounded_and_never_retried(monkeypatch, failure):
    client_type = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if failure == "redirect":
            return httpx.Response(307, headers={"location": "https://other.invalid"})
        if failure == "unauthorized":
            return httpx.Response(401)
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
        return httpx.Response(200, json={"message": {"content": ""}})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client_type(transport=httpx.MockTransport(handle), **kw)
    )
    with pytest.raises((httpx.HTTPError, ValueError)):
        asyncio.run(
            HTTPTextProvider().complete(
                Provider("test", "ollama", "http://localhost:11434", "x"), [], 0, 10
            )
        )
    assert len(requests) == 1
