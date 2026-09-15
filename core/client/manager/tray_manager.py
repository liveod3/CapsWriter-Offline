"""客户端托盘：常用动作、静态配置入口与无会话文本处理。"""

from __future__ import annotations
import asyncio
import os
import subprocess
import time
from pathlib import Path

from config_client import ClientConfig as Config
from core.ui.menu_model import MenuAction
from . import logger


class TrayManager:
    def __init__(self, app):
        self.app = app
        self._action_running = False

    @property
    def state(self):
        return self.app.state

    def _open(self, path):
        path = Path(path)
        if not path.exists() and not path.suffix:
            path.mkdir(parents=True, exist_ok=True)
        try:
            if path.name == "providers.toml":
                from core.client.llm.config import ensure_provider_file

                path = ensure_provider_file(path.parent)
            if path.suffix in {".py", ".toml"}:
                # 不调用 .py 默认关联，避免编辑配置意外执行 Python。
                subprocess.Popen(["notepad.exe", str(path)])
            else:
                os.startfile(str(path))
        except OSError as exc:
            logger.warning("Cannot open settings or history: %s", type(exc).__name__)
            from core.ui import show_status_hint

            show_status_hint("Could not open this file or folder.", duration_ms=2500)

    def menu_actions(self):
        root = self.app.base_dir
        text_enabled = lambda _item: bool(
            getattr(Config, "llm_enabled", False)
            and self.state.last_recognition_text
            and not self._action_running
        )
        return [
            MenuAction(
                lambda _item: "Resume dictation"
                if self.state.dictation_paused
                else "Pause dictation",
                self._toggle_pause,
                "Pause releases the microphone. Resume explicitly to use dictation again.",
                lambda _item: "resume" if self.state.dictation_paused else "pause",
            ),
            MenuAction(
                "Copy last result",
                self._copy_result,
                "Copy the most recent output to the clipboard.",
                "copy",
                enabled=lambda _item: bool(self.state.last_output_text),
            ),
            MenuAction(
                "Text actions",
                tooltip="Process the last transcript and copy the result.",
                icon="text",
                children=[
                    MenuAction(
                        "Correct transcription",
                        lambda: self._text_action("correct_asr"),
                        "Correct the last transcript and copy the complete result.",
                        "text",
                        text_enabled,
                    ),
                    MenuAction(
                        "Translate",
                        lambda: self._text_action("translate"),
                        "Translate the last transcript and copy the complete result.",
                        "translate",
                        text_enabled,
                    ),
                    MenuAction(
                        "Cancel text action",
                        self.app.llm.cancel,
                        "Cancel the current text request. Keep the original transcript.",
                        "pause",
                    ),
                ],
            ),
            MenuAction(
                "Open history",
                lambda: self._open(root / getattr(Config, "transcript_dir", "logs/transcripts")),
                "Open saved transcripts, organized by year and month.",
                "history",
            ),
            MenuAction(
                "Settings",
                tooltip="Edit client settings and text action configuration.",
                icon="settings",
                children=[
                    MenuAction(
                        "Client settings…",
                        lambda: self._open(root / "config_client.py"),
                        "Edit client settings. Restart the client to apply changes.",
                        "settings",
                    ),
                    MenuAction(
                        "Provider connections…",
                        lambda: self._open(self.app.llm.directory / "providers.toml"),
                        "Edit local connections and API keys. This file is excluded from Git.",
                        "settings",
                    ),
                    MenuAction(
                        "Text presets…",
                        lambda: self._open(self.app.llm.directory / "presets.toml"),
                        "Edit prompts and voice triggers. Changes apply to the next request.",
                        "text",
                    ),
                ],
            ),
            MenuAction(
                "Troubleshooting",
                tooltip="Reconnect the microphone or inspect diagnostics.",
                icon="tools",
                children=[
                    MenuAction(
                        "Reconnect microphone",
                        self._reconnect,
                        "Reopen the microphone. Resume dictation first if paused.",
                        "microphone",
                        enabled=lambda _item: not self.state.dictation_paused,
                    ),
                    MenuAction(
                        "Open diagnostic logs",
                        lambda: self._open(root / "logs" / "diagnostics"),
                        "Open dated diagnostic logs. These are separate from transcript history.",
                        "folder",
                    ),
                    MenuAction(
                        "Copy original transcription",
                        self._copy_original,
                        "Copy the last ASR result before any text action.",
                        "copy",
                        enabled=lambda _item: bool(self.state.last_recognition_text),
                    ),
                ],
            ),
        ]

    def start(self):
        if not Config.enable_tray:
            return
        from ..ui import enable_min_to_tray

        enable_min_to_tray(
            "CapsWriter Client",
            str(self.app.base_dir / "assets" / "client-icon.ico"),
            exit_callback=self.app.stop,
            more_options=self.menu_actions(),
        )

    def stop(self):
        if Config.enable_tray:
            from ..ui import stop_tray

            stop_tray()

    def _toggle_pause(self):
        self._schedule(asyncio.to_thread(self.app.toggle_dictation_pause))

    def _reconnect(self):
        async def reopen():
            if self.state.recording:
                from core.ui import show_status_hint

                show_status_hint(
                    "Finish recording before reconnecting the microphone.", duration_ms=2000
                )
                return
            if not self.state.dictation_paused:
                await asyncio.to_thread(self.app.stream.reopen)

        self._schedule(reopen())

    def _schedule(self, coroutine):
        loop = self.app.loop
        if loop.is_closed() or getattr(self.app, "_stopping", False):
            coroutine.close()
            return False
        try:
            asyncio.run_coroutine_threadsafe(coroutine, loop)
            return True
        except RuntimeError:
            coroutine.close()
            return False

    def _copy_result(self):
        if self.state.last_output_text:
            from core.client.clipboard import copy_to_clipboard

            copy_to_clipboard(self.state.last_output_text)

    def _copy_original(self):
        if self.state.last_recognition_text:
            from core.client.clipboard import copy_to_clipboard

            copy_to_clipboard(self.state.last_recognition_text)

    def _text_action(self, preset_id):
        text = self.state.last_recognition_text
        if not text or self._action_running:
            return
        self._action_running = True

        async def run():
            try:
                result = await self.app.llm.process(text, preset_id=preset_id)
                if result.cancelled or getattr(self.app, "_stopping", False):
                    return
                self.state.set_output_text(result.text)
                self._copy_result()
                if result.processed and getattr(Config, "save_llm_records", False):
                    await asyncio.to_thread(
                        self.app.action_records.write,
                        result.text,
                        time.time(),
                        action_input=result.input_text,
                    )
                from core.ui import show_status_hint

                show_status_hint(
                    (result.error_message or "Text action failed.") + " Original copied."
                    if result.error else "Result copied.",
                    duration_ms=5000 if result.error else 2500,
                )
            finally:
                self._action_running = False

        if not self._schedule(run()):
            self._action_running = False
