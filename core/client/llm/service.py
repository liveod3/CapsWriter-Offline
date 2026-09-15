"""独立文本动作：默认纠错或显式预设，无角色执行、历史或剪贴板采集。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import Catalog, load_catalog
from .settings import llm_options
from .provider import HTTPTextProvider, MissingAPIKeyError
from .errors import describe_failure


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
        if self._stopped or self._hotkeys or not getattr(self.config, "llm_enabled", False):
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
        self, text: str, *, context: str = "", preset_id: str | None = None,
        progress_callback=None,
    ) -> TextResult:
        if self._stopped or not getattr(self.config, "llm_enabled", False) or not text.strip():
            return TextResult(text, text)
        self._loop = asyncio.get_running_loop()
        epoch = self._cancel_epoch
        # 模式在请求入口固定，菜单切换只影响后续请求。
        default_preset = getattr(self.config, "llm_default_preset", "correct_asr")
        options = llm_options(self.config)
        if not any(options.values()) or (preset_id in options and not options[preset_id]):
            return TextResult(text, text)
        content = text
        selected_id = None
        started = time.monotonic()
        request_id = uuid.uuid4().hex[:8]
        phase = "configuration"
        from core.client import logger

        try:
            if progress_callback:
                progress_callback("Preparing LLM…")
            # 每次请求加载静态文件，编辑后下次请求生效；没有文件监控线程。
            catalog = await asyncio.to_thread(load_catalog, self.directory)
            if self._stopped or epoch != self._cancel_epoch:
                return TextResult(text, text, cancelled=True)
            # 关闭的能力不参加口令匹配，避免绕过开关或吞掉原文中的口令。
            catalog = Catalog(catalog.providers, {
                key: preset for key, preset in catalog.presets.items()
                if options.get(key, True)
            })
            if isinstance(default_preset, str) and not options.get(default_preset, True):
                # 旧菜单可能留下默认翻译；只开润色时应实际执行润色。
                # 不自动回退到翻译，避免普通听写意外变成另一种语言。
                default_preset = "correct_asr" if options["correct_asr"] else None
            if preset_id is not None:
                preset = catalog.presets[preset_id]
            else:
                preset, content = catalog.select(
                    text, default_preset
                )
            if preset is None:
                return TextResult(text, text)
            selected_id = preset.id
            provider = catalog.providers[preset.provider]
            host = urlsplit(provider.base_url).hostname
            location = "Local" if host in {"localhost", "127.0.0.1", "::1"} else "Remote"
            if self.status_callback and not progress_callback:
                self.status_callback(
                    f"{preset.name} · {provider.model} · {location}", duration_ms=2500
                )
            payload = {"transcript": content}
            if preset.use_caret_context and context:
                payload["surrounding_text_reference"] = context[:3500]
            messages = [
                {"role": "system", "content": preset.system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
            phase = "request"
            if progress_callback:
                progress_callback("Waiting for LLM…")
            logger.info("LLM request started: request=%s input_chars=%d preparation_ms=%d",
                        request_id, len(content), int((time.monotonic() - started) * 1000))
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
            logger.info("LLM request completed: request=%s elapsed_ms=%d output_chars=%d",
                        request_id, int((time.monotonic() - started) * 1000), len(result))
            return TextResult(result, content, selected_id, processed=True)
        except asyncio.CancelledError:
            logger.info("LLM request cancelled: request=%s phase=%s elapsed_ms=%d",
                        request_id, phase, int((time.monotonic() - started) * 1000))
            return TextResult(content, content, selected_id, cancelled=True)
        except Exception as exc:
            category, detail, fields = describe_failure(exc)
            if isinstance(exc, MissingAPIKeyError):
                category, detail = "missing_api_key", exc.user_message
            elif phase == "configuration":
                category, detail = "configuration_error", "LLM configuration could not be loaded. Check Provider and preset settings."
            logger.warning(
                "LLM action failed: request=%s phase=%s type=%s category=%s elapsed_ms=%d details=%s message=%s",
                request_id, phase, type(exc).__name__, category,
                int((time.monotonic() - started) * 1000), json.dumps(fields, sort_keys=True), detail,
            )
            return TextResult(
                content, content, selected_id, error=type(exc).__name__, error_message=detail
            )
