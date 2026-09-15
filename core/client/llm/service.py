"""独立文本动作：默认纠错或显式预设，无角色执行、历史或剪贴板采集。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import load_catalog
from .provider import HTTPTextProvider, MissingAPIKeyError


@dataclass(frozen=True)
class TextResult:
    text: str
    input_text: str
    preset_id: str | None = None
    processed: bool = False
    cancelled: bool = False
    error: str = ""
    error_message: str = ""


class TextActionService:
    def __init__(self, config, base_dir: Path, transport=None, status_callback=None):
        self.config = config
        self.directory = base_dir / getattr(config, "llm_config_dir", "LLM")
        self.transport = transport or HTTPTextProvider()
        self.status_callback = status_callback
        self._active: set[asyncio.Task] = set()
        self._loop = None
        self._stopped = False
        self._hotkeys = None
        self._stop_key = None
        self._cancel_epoch = 0

    def start(self):
        if not getattr(self.config, "llm_enabled", False):
            return
        from core.client.global_hotkey import get_global_hotkey_manager

        self._hotkeys = get_global_hotkey_manager()
        self._stop_key = "<" + getattr(self.config, "llm_stop_key", "esc").strip("<>").lower() + ">"
        self._hotkeys.register(self._stop_key, self.cancel)
        self._hotkeys.start()

    def cancel(self):
        self._cancel_epoch += 1
        if self._loop and not self._loop.is_closed():

            def cancel_tasks():
                for task in tuple(self._active):
                    task.cancel()

            self._loop.call_soon_threadsafe(cancel_tasks)

    def stop(self):
        self._stopped = True
        self.cancel()
        if self._hotkeys and self._stop_key:
            self._hotkeys.unregister(self._stop_key)
            self._hotkeys = None

    async def process(
        self, text: str, *, context: str = "", preset_id: str | None = None
    ) -> TextResult:
        if self._stopped or not getattr(self.config, "llm_enabled", False) or not text.strip():
            return TextResult(text, text)
        self._loop = asyncio.get_running_loop()
        epoch = self._cancel_epoch
        content = text
        selected_id = None
        try:
            # 每次请求加载静态文件，编辑后下次请求生效；没有文件监控线程。
            catalog = await asyncio.to_thread(load_catalog, self.directory)
            if self._stopped or epoch != self._cancel_epoch:
                return TextResult(text, text, cancelled=True)
            if preset_id is not None:
                preset = catalog.presets[preset_id]
            else:
                preset, content = catalog.select(
                    text, getattr(self.config, "llm_default_preset", "correct_asr")
                )
            if preset is None:
                return TextResult(text, text)
            selected_id = preset.id
            provider = catalog.providers[preset.provider]
            host = urlsplit(provider.base_url).hostname
            location = "Local" if host in {"localhost", "127.0.0.1", "::1"} else "Remote"
            if self.status_callback:
                self.status_callback(
                    f"{preset.name} · {provider.model} · {location}", duration_ms=5000
                )
            payload = {"transcript": content}
            if preset.use_caret_context and context:
                payload["surrounding_text_reference"] = context[:3500]
            messages = [
                {"role": "system", "content": preset.system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
            request = asyncio.create_task(
                self.transport.complete(provider, messages, preset.temperature, preset.max_tokens)
            )
            self._active.add(request)
            try:
                # 限制整个请求的耗时，防止持续发送少量数据绕过 HTTP 读取超时。
                async with asyncio.timeout(provider.timeout):
                    result = await request
            finally:
                self._active.discard(request)
            if self._stopped or epoch != self._cancel_epoch:
                return TextResult(content, content, selected_id, cancelled=True)
            return TextResult(result, content, selected_id, processed=True)
        except asyncio.CancelledError:
            return TextResult(content, content, selected_id, cancelled=True)
        except Exception as exc:
            # 仅展示受控的固定提示，其他异常可能携带 URL、凭据或响应正文。
            from core.client import logger

            detail = exc.user_message if isinstance(exc, MissingAPIKeyError) else ""
            logger.warning(
                "Text action failed: %s%s", type(exc).__name__, f". {detail}" if detail else ""
            )
            return TextResult(
                content, content, selected_id, error=type(exc).__name__, error_message=detail
            )
