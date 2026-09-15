"""无会话异步传输；一次调用只有一组消息，取消会关闭当前请求。"""

from __future__ import annotations

import os
from typing import Protocol

from .config import Provider


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
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise ValueError("LLM response exceeds limit")
        import json

        result = json.loads(data)
        if provider.kind == "ollama":
            text = result["message"]["content"]
        else:
            text = result["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("LLM returned no text")
        return text.strip()
