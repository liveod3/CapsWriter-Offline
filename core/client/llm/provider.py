"""无会话异步传输；一次调用只有一组消息，取消会关闭当前请求。"""

from __future__ import annotations

import os
import json
from typing import Protocol

from .config import Provider
from .errors import LLMResponseError, api_error, generation_error


class MissingAPIKeyError(ValueError):
    """仅使用固定提示，允许界面和日志安全展示，不携带配置内容。"""

    def __init__(self, *, from_environment: bool):
        source = "environment variable" if from_environment else "local configuration"
        super().__init__(f"Configured API key {source} is empty")
        self.user_message = (
            "API key missing. Set the environment variable selected by api_key_env, "
            "then restart Client."
            if from_environment
            else "API key missing. Fill api_key in providers.toml "
            "via Settings > Provider connections."
        )


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
        # 不重试认证/配置错误，不允许重定向携带认证与文本到另一个地址。
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
                        raise LLMResponseError("response_too_large", "LLM response exceeds the size limit.")
                    data.extend(chunk)
                try:
                    result = json.loads(data)
                except (ValueError, UnicodeError):
                    if not response.is_success:
                        raise api_error(response.status_code, {}) from None
                    raise LLMResponseError("invalid_json", "The provider returned invalid JSON.") from None
                if not response.is_success or (isinstance(result, dict) and "error" in result):
                    raise api_error(response.status_code, result, response.headers.get("retry-after", ""))

        if not isinstance(result, dict):
            raise LLMResponseError("invalid_response", "The provider returned an unexpected response structure.")
        feedback = result.get("promptFeedback", {})
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise generation_error(feedback["blockReason"], "block_reason")
        # 兼容端点/代理若透传原生 Gemini 失败字段，也保留结束原因；不将其误报为 KeyError。
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
                raise LLMResponseError("empty_choices", "The provider returned no output candidates.")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise LLMResponseError("invalid_response", "The provider returned an invalid output candidate.")
            reason = choice.get("finish_reason")
            if reason and reason != "stop":
                raise generation_error(reason, "finish_reason")
            message = choice.get("message")
        if not isinstance(message, dict):
            raise LLMResponseError("invalid_response", "The provider returned no text message.")
        if message.get("refusal"):
            raise generation_error("CONTENT_BLOCKED", "finish_reason")
        if message.get("tool_calls") or message.get("function_call"):
            raise generation_error("TOOL_CALLS", "finish_reason")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise LLMResponseError("empty_output", "The provider returned no usable text.")
        return text.strip()
