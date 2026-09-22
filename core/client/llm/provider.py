"""Send one stateless asynchronous request; cancellation closes the request."""

from __future__ import annotations

from core.i18n import tr

import os
import json
from typing import Protocol

from .config import Provider
from .errors import LLMResponseError, api_error, generation_error


class MissingAPIKeyError(ValueError):
    """Expose fixed, safe messages without including configuration content."""

    def __init__(self, *, from_environment: bool):
        source = "environment variable" if from_environment else "local configuration"
        super().__init__(f"Configured API key {source} is empty")
        self.message_id = "llm.missing_env_key" if from_environment else "llm.missing_local_key"

    @property
    def user_message(self):
        return tr(self.message_id)



class TextProvider(Protocol):
    async def complete(
        self, provider: Provider, messages: list[dict], temperature: float, max_tokens: int
    ) -> str: ...


class HTTPTextProvider:
    async def complete(
        self, provider: Provider, messages: list[dict], temperature: float, max_tokens: int
    ) -> str:
        import httpx

        key = (
            os.environ.get(provider.api_key_env, "")
            if provider.api_key_env
            else (provider.api_key or "")
        ).strip()
        if (provider.api_key_env or provider.api_key is not None) and not key:
            raise MissingAPIKeyError(from_environment=bool(provider.api_key_env))
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if provider.kind == "ollama":
            url = provider.base_url + "/api/chat"
            payload = {
                "model": provider.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
        else:
            url = provider.base_url + "/chat/completions"
            payload = {
                "model": provider.model,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        # Do not retry auth/configuration failures or redirect credentials and text elsewhere.
        async with httpx.AsyncClient(
            timeout=provider.timeout, follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                data = bytearray()
                limit = 64 * 1024 if response.is_error else 2 * 1024 * 1024
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > limit:
                        if not response.is_success:
                            raise api_error(response.status_code, {})
                        raise LLMResponseError("response_too_large", "llm.response_too_large")
                    data.extend(chunk)
                try:
                    result = json.loads(data)
                except (ValueError, UnicodeError):
                    if not response.is_success:
                        raise api_error(response.status_code, {}) from None
                    raise LLMResponseError("invalid_json", "llm.invalid_json") from None
                if not response.is_success or (isinstance(result, dict) and "error" in result):
                    raise api_error(response.status_code, result, response.headers.get("retry-after", ""))

        if not isinstance(result, dict):
            raise LLMResponseError("invalid_response", "llm.invalid_structure")
        feedback = result.get("promptFeedback", {})
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise generation_error(feedback["blockReason"], "block_reason")
        # Preserve native Gemini failure reasons passed through compatible proxies.
        candidates = result.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            reason = candidates[0].get("finishReason")
            if reason and reason != "STOP":
                raise generation_error(reason, "finish_reason")
        if provider.kind == "ollama":
            reason = result.get("done_reason")
            if reason and reason != "stop":
                raise generation_error(reason, "finish_reason")
            message = result.get("message")
        else:
            choices = result.get("choices")
            if not isinstance(choices, list) or not choices:
                raise LLMResponseError("empty_choices", "llm.empty_choices")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise LLMResponseError("invalid_response", "llm.invalid_candidate")
            reason = choice.get("finish_reason")
            if reason and reason != "stop":
                raise generation_error(reason, "finish_reason")
            message = choice.get("message")
        if not isinstance(message, dict):
            raise LLMResponseError("invalid_response", "llm.no_message")
        if message.get("refusal"):
            raise generation_error("CONTENT_BLOCKED", "finish_reason")
        if message.get("tool_calls") or message.get("function_call"):
            raise generation_error("TOOL_CALLS", "finish_reason")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise LLMResponseError("empty_output", "llm.empty_output")
        return text.strip()
