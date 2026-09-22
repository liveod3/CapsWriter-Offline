"""Run one default or explicit text action without roles, history, or clipboard capture."""

from __future__ import annotations

from core.i18n import Notice, tr

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
from .errors import describe_failure, localized_failure
from core.llm_accounting.ledger import CostLedger
from core.llm_accounting.usage import UsageObservation, observation, estimate_tokens


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
        self.costs = CostLedger(base_dir, self.directory)
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
        # Snapshot the mode at entry; menu changes apply to subsequent requests.
        default_preset = getattr(self.config, "llm_default_preset", "correct_asr")
        options = llm_options(self.config)
        if not any(options.values()) or (preset_id in options and not options[preset_id]):
            return TextResult(text, text)
        content = text
        selected_id = None
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        phase = "configuration"
        ticket = None
        observed = UsageObservation()
        outcome = 'failed'
        failure_category = None
        from core.client import logger

        try:
            if progress_callback:
                progress_callback('status.prepare_llm')
            # Reload static files per request; no file-watching thread is needed.
            catalog = await asyncio.to_thread(load_catalog, self.directory)
            if self._stopped or epoch != self._cancel_epoch:
                return TextResult(text, text, cancelled=True)
            # Exclude disabled capabilities from matching so triggers cannot bypass switches.
            catalog = Catalog(catalog.providers, {
                key: preset for key, preset in catalog.presets.items()
                if options.get(key, True)
            })
            if isinstance(default_preset, str) and not options.get(default_preset, True):
                # Legacy menus may leave translation as default; correction-only mode must correct.
                # Never fall back to translation and unexpectedly change the dictation language.
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
            location = tr('llm.local') if host in {"localhost", "127.0.0.1", "::1"} else tr('llm.remote')
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
            if getattr(self.config, 'llm_cost_tracking', True):
                try:
                    ticket, invalid_config = await asyncio.to_thread(
                        self.costs.prepare, provider, messages, preset.max_tokens, request_id, preset.id
                    )
                    if invalid_config:
                        logger.warning(Notice('cost.config_fallback'))
                except Exception:
                    logger.warning(Notice('cost.write_failed'))
            if self._stopped or epoch != self._cancel_epoch:
                outcome = 'not_sent'
                failure_category = 'cancelled_before_dispatch'
                return TextResult(content, content, selected_id, cancelled=True)
            phase = "request"
            if progress_callback:
                progress_callback('status.wait_llm')
            logger.info(Notice('diagnostic.service.llm_request_started_request_input_chars_preparation_ms'),
                        request_id, len(content), int((time.monotonic() - started) * 1000))
            async def complete():
                token = observation.set((observed, ticket[1]['rate'] if ticket else None))
                try:
                    if not isinstance(self.transport, HTTPTextProvider):
                        observed.sent = True
                    return await self.transport.complete(
                        provider, messages, preset.temperature, preset.max_tokens
                    )
                finally:
                    observation.reset(token)

            request = asyncio.create_task(complete())
            self._active.add(request)
            try:
                # Bound the entire request so a trickling response cannot evade the HTTP read timeout.
                async with asyncio.timeout(provider.timeout):
                    result = await request
            finally:
                self._active.discard(request)
            if self._stopped or epoch != self._cancel_epoch:
                outcome = 'cancelled'
                return TextResult(content, content, selected_id, cancelled=True)
            outcome = 'completed'
            observed.output_estimate = estimate_tokens(result)
            logger.info(Notice('diagnostic.service.llm_request_completed_request_elapsed_ms_output_chars'),
                        request_id, int((time.monotonic() - started) * 1000), len(result))
            return TextResult(result, content, selected_id, processed=True)
        except asyncio.CancelledError:
            outcome = 'cancelled'
            logger.info(Notice('diagnostic.service.llm_request_cancelled_request_phase_elapsed_ms'),
                        request_id, phase, int((time.monotonic() - started) * 1000))
            return TextResult(content, content, selected_id, cancelled=True)
        except Exception as exc:
            category, detail, fields = describe_failure(exc)
            if isinstance(exc, MissingAPIKeyError):
                outcome = 'not_sent'
                category, detail = "missing_api_key", tr(exc.message_id, locale="en")
                user_detail = exc.user_message
            elif phase == "configuration":
                category, detail = "configuration_error", tr("llm.configuration_error", locale="en")
                user_detail = tr("llm.configuration_error")
            else:
                user_detail = localized_failure(exc)
            failure_category = category
            logger.warning(
                Notice('diagnostic.service.llm_action_failed_request_phase_type_category_elapsed'),
                request_id, phase, type(exc).__name__, category,
                int((time.monotonic() - started) * 1000), json.dumps(fields, sort_keys=True), detail,
            )
            return TextResult(
                content, content, selected_id, error=type(exc).__name__, error_message=user_detail
            )
        finally:
            if ticket:
                try:
                    await asyncio.to_thread(
                        self.costs.finish, ticket, observed, outcome, failure_category,
                        int((time.monotonic() - started) * 1000),
                    )
                except Exception:
                    # Accounting errors must never discard successful text or cause a retry.
                    logger.warning(Notice('cost.write_failed'))
