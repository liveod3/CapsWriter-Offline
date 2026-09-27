"""UI-independent client operations on the application's existing owners."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.client.llm.settings import llm_options
from core.config_reload import CandidateError
from core.i18n import Notice
from core.settings import SettingsService


class ClientOperations:
    """Async operations run on the client loop; submit() adapts other UI threads.

    Configuration writes only persist candidates. The existing reloader publishes
    at the recording/file boundary; no operation recreates ASR or aligner owners.
    """

    def __init__(self, app):
        self.app = app
        self.settings = SettingsService(app.config_reload)
        self._settings_lock = asyncio.Lock()

    def submit(self, coroutine):
        """Return a concurrent Future, or None when the owner is shutting down."""
        if self.app.loop.is_closed() or getattr(self.app, '_stopping', False):
            coroutine.close()
            return None
        try:
            return asyncio.run_coroutine_threadsafe(coroutine, self.app.loop)
        except RuntimeError:
            coroutine.close()
            return None

    def _check_running(self):
        if getattr(self.app, '_stopping', False):
            raise CandidateError(Notice('settings.stopped'))

    async def read_settings(self):
        return await asyncio.to_thread(self.settings.read)

    async def validate_settings(self, changes, *, revision):
        return await asyncio.to_thread(self.settings.validate, changes, revision=revision)

    async def save_settings(self, changes, *, revision):
        async with self._settings_lock:
            self._check_running()
            return await asyncio.to_thread(self.settings.save, changes, revision=revision)

    async def set_language(self, language):
        async with self._settings_lock:
            self._check_running()
            snapshot = await self.read_settings()
            if snapshot.error:
                raise CandidateError(snapshot.error)
            return await asyncio.to_thread(
                self.settings.save, {'ui_language': language}, revision=snapshot.revision,
            )

    async def toggle_llm(self, preset_id=None):
        async with self._settings_lock:
            self._check_running()
            snapshot = await self.read_settings()
            if snapshot.error:
                raise CandidateError(snapshot.error)
            # A second click edits the saved preference, even if an active task
            # has delayed publication of the first click.
            options = llm_options(SimpleNamespace(**snapshot.saved['ClientConfig']))
            if preset_id is None:
                options = dict.fromkeys(options, not all(options.values()))
            elif preset_id in options:
                options[preset_id] = not options[preset_id]
            else:
                raise CandidateError(Notice('settings.unknown_field'))
            return await asyncio.to_thread(self.settings.save, {
                'llm_enabled': any(options.values()),
                'llm_correction_enabled': options['correct_asr'],
                'llm_translation_enabled': options['translate'],
            }, revision=snapshot.revision)

    async def toggle_pause(self):
        self._check_running()
        return await asyncio.to_thread(self.app.toggle_dictation_pause)

    async def reconnect_microphone(self):
        self._check_running()
        if self.app.state.recording:
            return Notice('mic.finish_first')
        if not self.app.state.dictation_paused:
            await asyncio.to_thread(self.app.stream.reopen)
        return None
