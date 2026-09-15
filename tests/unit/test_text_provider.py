import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest

from core.client.llm.config import Provider
from core.client.llm.provider import HTTPTextProvider


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
