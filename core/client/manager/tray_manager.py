"""Provide client tray actions, static configuration, and stateless text processing."""

from __future__ import annotations

from core.i18n import LANGUAGES, Notice, lazy, tr

import os
import subprocess
from pathlib import Path

from config_client import ClientConfig as Config
from core.client.llm.settings import llm_options
from core.ui.menu_model import MenuAction
from core.diagnostics import storage_path
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
                # Avoid the .py file association, which could execute the configuration instead of editing it.
                subprocess.Popen(["notepad.exe", str(path)])
            else:
                os.startfile(str(path))
        except OSError as exc:
            logger.warning(Notice('diagnostic.tray_manager.cannot_open_settings_or_history'), type(exc).__name__)
            from core.ui import show_status_hint

            show_status_hint(tr('settings.open_failed'), duration_ms=2500)

    def menu_actions(self):
        root = self.app.base_dir
        return [
            MenuAction(
                lambda _item: tr('tray.resume')
                if self.state.dictation_paused
                else tr('tray.pause'),
                self._toggle_pause,
                lazy('tray.pause.tip'),
                lambda _item: "resume" if self.state.dictation_paused else "pause",
            ),
            MenuAction(
                lazy('tray.copy'),
                self._copy_result,
                lazy('tray.copy.tip'),
                "copy",
                enabled=lambda _item: bool(self.state.last_output_text),
            ),
            MenuAction(
                lazy('tray.llm'),
                tooltip=lazy('tray.llm.tip'),
                icon="text",
                children=[
                    MenuAction(
                        lambda _item: self._llm_toggle_label(),
                        lambda: self._toggle_llm_option(),
                        lazy('tray.llm.all.tip'),
                        "pause",
                        enabled=lambda _item: not self._mode_saving,
                        checked=lambda _item: all(llm_options(Config).values()),
                    ),
                    MenuAction(
                        lambda _item: self._llm_toggle_label("correct_asr"),
                        lambda: self._toggle_llm_option("correct_asr"),
                        lazy('tray.llm.correction.tip'),
                        "text",
                        enabled=lambda _item: not self._mode_saving,
                        checked=lambda _item: llm_options(Config)["correct_asr"],
                    ),
                    MenuAction(
                        lambda _item: self._llm_toggle_label("translate"),
                        lambda: self._toggle_llm_option("translate"),
                        lazy('tray.llm.translation.tip'),
                        "translate",
                        enabled=lambda _item: not self._mode_saving,
                        checked=lambda _item: llm_options(Config)["translate"],
                    ),
                ],
            ),
            MenuAction(
                lazy('tray.history'),
                lambda: self._open(storage_path(root, getattr(Config, "transcript_dir", "records/transcripts"))),
                lazy('tray.history.tip'),
                "history",
            ),
            MenuAction(
                lazy('tray.recordings'),
                self._open_recordings,
                lazy('tray.recordings.tip'),
                "folder",
            ),
            MenuAction(
                lazy('tray.settings'),
                tooltip=lazy('tray.settings.tip'),
                icon="settings",
                children=[
                    MenuAction(
                        lazy('language.title'), tooltip=lazy('language.tip'), icon='translate',
                        children=[
                            MenuAction(
                                lazy(label), self._language_callback(language),
                                checked=lambda _item, language=language: getattr(Config, 'ui_language', 'auto') == language,
                                radio=True, enabled=lambda _item: not self._mode_saving,
                            )
                            for language, label in LANGUAGES.items()
                        ],
                    ),
                    MenuAction(
                        lazy('gui.open'), self._open_settings, icon='settings',
                    ),
                    MenuAction(
                        lazy('tray.client_settings'),
                        lambda: self._open(root / "config_client.py"),
                        lazy('tray.client_settings.tip'),
                        "settings",
                    ),
                    MenuAction(
                        lazy('tray.providers'),
                        lambda: self._open(self.app.llm.directory / "providers.toml"),
                        lazy('tray.providers.tip'),
                        "settings",
                    ),
                    MenuAction(
                        lazy('tray.presets'),
                        lambda: self._open(self.app.llm.directory / "presets.toml"),
                        lazy('tray.presets.tip'),
                        "text",
                    ),
                ],
            ),
            MenuAction(
                lazy('tray.troubleshoot'),
                tooltip=lazy('tray.troubleshoot.tip'),
                icon="tools",
                children=[
                    MenuAction(
                        lazy('tray.reconnect'),
                        self._reconnect,
                        lazy('tray.reconnect.tip'),
                        "microphone",
                        enabled=lambda _item: not self.state.dictation_paused,
                    ),
                    MenuAction(
                        lazy('tray.logs'),
                        lambda: self._open(storage_path(
                            root, getattr(Config, "diagnostic_log_dir", "logs")) / "client"),
                        lazy('tray.logs.tip'),
                        "folder",
                    ),
                    MenuAction(
                        lazy('tray.original'),
                        self._copy_original,
                        lazy('tray.original.tip'),
                        "copy",
                        enabled=lambda _item: bool(self.state.last_recognition_text),
                    ),
                ],
            ),
        ]

    def start(self):
        if getattr(self.app, 'desktop_mode', False):
            return
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
        if getattr(self.app, 'desktop_mode', False):
            return
        if Config.enable_tray:
            from ..ui import stop_tray

            stop_tray()

    def _toggle_pause(self):
        self._schedule(self.app.operations.toggle_pause())

    def _open_settings(self):
        import asyncio
        self._schedule(asyncio.to_thread(self.app.settings_window.open))

    def _reconnect(self):
        async def reopen():
            notice = await self.app.operations.reconnect_microphone()
            if notice:
                from core.ui import show_status_hint
                from core.i18n import localize_notice
                show_status_hint(localize_notice(notice), duration_ms=2000)

        self._schedule(reopen())

    def _schedule(self, coroutine):
        future = self.app.operations.submit(coroutine)
        if future is None:
            return False

        def completed(result):
            if result.cancelled() or getattr(self.app, '_stopping', False):
                return
            error = result.exception()
            if error is not None:
                logger.warning(Notice('diagnostic.tray_manager.action_failed'), type(error).__name__)
                self._save_failure(error, 'settings.action_failed', duration_ms=2500)

        future.add_done_callback(completed)
        return True

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
            state = 'on' if all(options.values()) else 'partial' if active else 'off'
            return tr('llm.toggle.all', state=tr('state.' + state))
        active = options[preset_id]
        return tr('llm.toggle.' + preset_id, state=tr('state.on' if active else 'state.off'))

    def _language_callback(self, language):
        return lambda: self._set_language(language)

    def _set_language(self, language):
        async def save():
            from core.ui import show_status_hint

            if self._mode_saving:
                return
            self._mode_saving = True
            try:
                await self.app.operations.set_language(language)
                if not getattr(self.app, '_stopping', False):
                    show_status_hint(tr('language.saved'), duration_ms=2500)
            except (OSError, ValueError, SyntaxError) as exc:
                logger.warning(Notice('diagnostic.tray_manager.cannot_save_ui_language'), type(exc).__name__)
                if not getattr(self.app, '_stopping', False):
                    self._save_failure(exc, 'language.failed', duration_ms=3000)
            finally:
                self._mode_saving = False

        self._schedule(save())

    def _toggle_llm_option(self, preset_id=None):
        async def save():
            from core.ui import show_status_hint

            if self._mode_saving:
                return
            self._mode_saving = True
            try:
                await self.app.operations.toggle_llm(preset_id)
                if getattr(self.app, "_stopping", False):
                    return
                show_status_hint(tr('llm.saved_pending'), duration_ms=2500)
            except (OSError, ValueError, SyntaxError) as exc:
                logger.warning(Notice('diagnostic.tray_manager.cannot_save_llm_options'), type(exc).__name__)
                if not getattr(self.app, '_stopping', False):
                    self._save_failure(exc, 'llm.save_failed', duration_ms=2500)
            finally:
                self._mode_saving = False

        self._schedule(save())

    @staticmethod
    def _save_failure(exc, fallback, *, duration_ms):
        from core.config_reload import CandidateError
        from core.i18n import localize_notice
        from core.ui import show_status_hint

        message = localize_notice(exc.args[0]) if isinstance(exc, CandidateError) else tr(fallback)
        show_status_hint(message, duration_ms=duration_ms)
