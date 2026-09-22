"""客户端托盘：常用动作、静态配置入口与无会话文本处理。"""

from __future__ import annotations
import asyncio
import os
import subprocess
from pathlib import Path

from config_client import ClientConfig as Config
from core.client.llm.settings import llm_options, save_llm_options
from core.ui.menu_model import MenuAction
from . import logger


class TrayManager:
    def __init__(self, app):
        self.app = app
        self._mode_saving = False

    @property
    def state(self):
        return self.app.state

    def _open(self, path):
        path = Path(path)
        try:
            if not path.exists() and not path.suffix:
                path.mkdir(parents=True, exist_ok=True)
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
                "LLM actions",
                tooltip="Enable or disable all LLM actions, or control each action separately.",
                icon="text",
                children=[
                    MenuAction(
                        lambda _item: self._llm_toggle_label(),
                        lambda: self._toggle_llm_option(),
                        "Click to turn all off when all are on; otherwise turn all on. Changes are saved.",
                        "pause",
                        enabled=lambda _item: not self._mode_saving,
                        checked=lambda _item: all(llm_options(Config).values()),
                    ),
                    MenuAction(
                        lambda _item: self._llm_toggle_label("correct_asr"),
                        lambda: self._toggle_llm_option("correct_asr"),
                        "Click to toggle correction. The label shows its current state. Changes are saved.",
                        "text",
                        enabled=lambda _item: not self._mode_saving,
                        checked=lambda _item: llm_options(Config)["correct_asr"],
                    ),
                    MenuAction(
                        lambda _item: self._llm_toggle_label("translate"),
                        lambda: self._toggle_llm_option("translate"),
                        "Click to toggle translation. The label shows its current state. Changes are saved.",
                        "translate",
                        enabled=lambda _item: not self._mode_saving,
                        checked=lambda _item: llm_options(Config)["translate"],
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
                "Open recordings",
                self._open_recordings,
                "Open the configured recording folder. Existing recordings stay in their original folders.",
                "folder",
            ),
            MenuAction(
                "Settings",
                tooltip="Edit client settings and LLM action configuration.",
                icon="settings",
                children=[
                    MenuAction(
                        "Client settings…",
                        lambda: self._open(root / "config_client.py"),
                        "Supported changes apply when tasks finish. Check the console for restart requirements.",
                        "settings",
                    ),
                    MenuAction(
                        "Provider connections…",
                        lambda: self._open(self.app.llm.directory / "providers.toml"),
                        "Edit local connections and API keys. This file is excluded from Git.",
                        "settings",
                    ),
                    MenuAction(
                        "LLM presets…",
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
                        "Copy the last ASR result before any LLM action.",
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

    def _open_recordings(self):
        from core.client.audio.storage import recording_directory
        self._open(recording_directory(Config, self.app.base_dir))

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

    def _llm_toggle_label(self, preset_id=None):
        options = llm_options(Config)
        if preset_id is None:
            active = any(options.values())
            state = "on" if all(options.values()) else "partly on" if active else "off"
            return f"All LLM actions: Currently {state}"
        active = options[preset_id]
        name = {"correct_asr": "Correction", "translate": "Translation"}[preset_id]
        return f"{name}: Currently {'on' if active else 'off'}"

    def _toggle_llm_option(self, preset_id=None):
        async def save():
            from core.ui import show_status_hint

            if self._mode_saving:
                return
            self._mode_saving = True
            try:
                options = llm_options(Config)
                if preset_id is None:
                    active = not all(options.values())
                    options = dict.fromkeys(options, active)
                else:
                    options[preset_id] = not options[preset_id]
                await asyncio.to_thread(
                    save_llm_options, self.app.base_dir / "config_client.py",
                    correction=options["correct_asr"], translation=options["translate"],
                )
                if getattr(self.app, "_stopping", False):
                    return
                if hasattr(self.app, 'config_reload'):
                    show_status_hint("LLM settings saved; apply after current tasks finish.", duration_ms=2500)
                else:
                    Config.llm_correction_enabled = options["correct_asr"]
                    Config.llm_translation_enabled = options["translate"]
                    Config.llm_enabled = any(options.values())
                    if Config.llm_enabled:
                        self.app.llm.start()
                    show_status_hint("LLM settings saved.", duration_ms=1600)
            except (OSError, ValueError, SyntaxError) as exc:
                logger.warning("Cannot save LLM options: %s", type(exc).__name__)
                show_status_hint("Could not save LLM options. Check client settings.", duration_ms=2500)
            finally:
                self._mode_saving = False

        self._schedule(save())
