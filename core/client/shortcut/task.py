# coding: utf-8
"""
Shortcut tasks.

Track recording state for one shortcut.
"""

from __future__ import annotations

from core.i18n import Notice, tr

import asyncio
from concurrent.futures import Future
import time
from threading import Event, Thread
from typing import TYPE_CHECKING, Optional

from . import logger
from core.client.state import console
from core.tools.my_status import Status
from core.ui.recording_indicator import (
    hide_recording_indicator,
    hide_status_hint,
    show_recording_indicator,
    show_status_hint,
)
from core.ui.tray import set_recording_state
from core.client.audio.capture import CaptureSession
from core.client.dictation_lifecycle import MAX_PENDING_DICTATIONS, DictationSendError
 
if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.audio.recorder import AudioRecorder
    from core.client.app import CapsWriterClient



class ShortcutTask:
    """
    Recording task owned by one shortcut.

    Keep each shortcut's recording state independent.
    """

    AUDIO_READY_TIMEOUT = 5.0
    MAX_PENDING_RECORDERS = 8

    def __init__(self, app: CapsWriterClient, shortcut: Shortcut, recorder_class=None):
        """
        Initialize a shortcut task.

        Args:
            app: Client App instance.
            shortcut: Shortcut configuration.
            recorder_class: Optional AudioRecorder class for lazy loading.
        """
        self.app = app
        self.shortcut = shortcut
        self._recorder_class = recorder_class

        # Task state.
        self.task: Optional[Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False
        self._launch_generation: int = 0
        self._progress_id: str | None = None
        self._capture = None
        self._pending_recorders = set()
        self._ready_cancel = Event()

        # Track hold-mode state.
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()

        # Countdown thread pool.
        self.pool = None

        # Recording animation.
        self._status = Status(message_id='mic.recording', spinner='point')

    @property
    def state(self) -> ClientState:
        """Access shared client state."""
        return self.app.state

    def _get_recorder(self) -> AudioRecorder:
        """Return the AudioRecorder instance."""
        if self._recorder_class is None:
            from core.client.audio.recorder import AudioRecorder
            self._recorder_class = AudioRecorder
        return self._recorder_class(self.app)

    def _show_recording_ready(self, generation: int, clear_preparing_hint: bool) -> None:
        """Publish readiness only while this generation owns capture."""
        if generation != self._launch_generation or not self.is_recording:
            return
        with self.state.recording_lock:
            if generation != self._launch_generation or not self.is_recording:
                return
            if clear_preparing_hint:
                hide_status_hint()
            self._status.start()
            show_recording_indicator()
            set_recording_state(True)

    def _wait_for_audio_ready(self, ready_event: Event, generation: int) -> None:
        """Wait off the shortcut thread and reset ownership after a timeout."""
        deadline = time.monotonic() + self.AUDIO_READY_TIMEOUT
        while generation == self._launch_generation and self.is_recording:
            ready_event = self.app.stream.get_ready_event()
            if self.app.stream.is_ready(ready_event):
                self._show_recording_ready(generation, clear_preparing_hint=True)
                return
            if time.monotonic() >= deadline:
                break
            if self._ready_cancel.wait(timeout=0.05):
                return
        if generation != self._launch_generation or not self.is_recording:
            return
        with self.state.recording_lock:
            if generation != self._launch_generation or not self.is_recording:
                return
            self.cancel()
            logger.warning(Notice('diagnostic.task.microphone_readiness_timed_out_capture_released'))
            show_status_hint(tr('mic.ready_timeout'), duration_ms=2200, dot_color='#EF4444')

    def launch(self) -> bool:
        """Atomically claim the microphone across all shortcut tasks."""
        self.app.mark_user_activity()
        with self.state.recording_lock:
            if getattr(self.app, '_stopping', False) or self.state.recording_owner is not None:
                return False
            if len(self.state.dictation_uploads) >= MAX_PENDING_DICTATIONS:
                show_status_hint(tr('mic.processing_pending'),
                                 duration_ms=2200)
                return False
            if max(len(self.state.recording_futures), len(self.state.recording_tasks)) >= self.MAX_PENDING_RECORDERS:
                show_status_hint(tr('mic.sending_pending'),
                                 duration_ms=2200)
                return False
            return self._launch_locked()

    def _launch_locked(self) -> bool:
        self._launch_generation += 1
        generation = self._launch_generation

        if getattr(self.state, 'dictation_manually_paused', False):
            show_status_hint(tr('mic.paused_help'), duration_ms=2000)
            return False
        if not self.app.loop.is_running() or self.app.loop.is_closed():
            return False
        capture = None
        try:
            from core.client.caret_context import foreground_window
            target_window = foreground_window()
            recorder = self._get_recorder()
            self.recording_start_time = time.time()
            capture = CaptureSession(self.app.loop, self.recording_start_time, target_window)
            self._capture = capture
            self._ready_cancel = Event()
            self._progress_id = recorder.task_id
            self.is_recording = True
            self.state.recording_owner = self
            self.state.capture = capture
            self.state.start_recording(self.recording_start_time)
            self.task = None
            coroutine = self._run_recorder(recorder, capture)
            try:
                self.task = asyncio.run_coroutine_threadsafe(coroutine, self.app.loop)
            except RuntimeError:
                coroutine.close()
                raise
            self._pending_recorders.add(self.task)
            self.state.recording_futures.add(self.task)
            self.state.recorder_by_id[recorder.task_id] = self.task
            self.task.add_done_callback(
                lambda future: self._recorder_done(future, capture, recorder.task_id)
            )
        except Exception as exc:
            if capture is not None and self._capture is capture:
                self.cancel()
            logger.error(Notice('diagnostic.task.could_not_start_recording', value0=type(exc).__name__))
            return False

        if not self.is_recording:
            return False
        # Hardware may still wake after start(); report readiness only after the first audio block.
        ready_event = self.app.stream.get_ready_event()
        if self.app.stream.is_ready(ready_event):
            self._show_recording_ready(generation, clear_preparing_hint=False)
        else:
            message = tr('mic.preparing')
            logger.info(Notice('diagnostic.task.microphone_preparing_shortcut'), self.shortcut.key)
            console.print(f'\n[ui.warning]●[/] [ui.value]{message}[/]')
            show_status_hint(message, duration_ms=5000, dot_color='#F59E0B')
            Thread(
                target=self._wait_for_audio_ready,
                args=(ready_event, generation),
                daemon=True,
                name=f'audio-ready-{self.shortcut.key}',
            ).start()

        return True

    async def _run_recorder(self, recorder, capture):
        """Track actual cleanup, not just the cancellable cross-thread Future."""
        operation = asyncio.current_task()
        self.state.recording_tasks.add(operation)
        try:
            if self.state.dictation_paused:
                resume = asyncio.create_task(asyncio.to_thread(
                    self.app.resume_dictation, show_hint=False, silent_stream=True))
                try:
                    resumed = await asyncio.shield(resume)
                except asyncio.CancelledError:
                    # Do not report cleanup complete while hardware is still opening.
                    while not resume.done():
                        try:
                            await asyncio.shield(resume)
                        except asyncio.CancelledError:
                            continue
                    raise
                if not resumed:
                    raise RuntimeError('MicrophoneResumeFailed')
            await recorder.record_and_send(capture)
        finally:
            self.state.recording_tasks.discard(operation)

    def _release_capture_locked(self) -> None:
        """Release only this task's current microphone ownership."""
        self._launch_generation += 1
        self._ready_cancel.set()
        self.is_recording = False
        if self.state.recording_owner is self and self.state.capture is self._capture:
            self.state.capture = None
            self.state.recording_owner = None
            self.state.stop_recording()
            self._status.stop()
            hide_recording_indicator()
            hide_status_hint()
            set_recording_state(False)

    def _recorder_done(self, future, capture, progress_id):
        """Ignore old completion callbacks when a newer recording is active."""
        error = None if future.cancelled() else future.exception()
        with self.state.recording_lock:
            self._pending_recorders.discard(future)
            self.state.recording_futures.discard(future)
            if self.state.recorder_by_id.get(progress_id) is future:
                self.state.recorder_by_id.pop(progress_id, None)
            if self.task is future:
                self.task = None
            if self._capture is capture and self.is_recording:
                capture.cancel()
                self._release_capture_locked()
                self.app.progress.finish(progress_id)
            if error is not None:
                logger.error(Notice('diagnostic.task.recording_worker_failed', value0=type(error).__name__))
                if self.state.recording_owner is None and not getattr(self.app, '_stopping', False):
                    message = (tr('mic.overflow')
                               if str(error) == 'CaptureBufferOverflow' else
                               tr('mic.failed'))
                    if isinstance(error, DictationSendError):
                        message = tr('mic.send_failed')
                    show_status_hint(message, duration_ms=3500, dot_color='#EF4444')

    def cancel(self) -> None:
        """Cancel this recording without changing another shortcut's state."""
        with self.state.recording_lock:
            self.app.mark_user_activity()
            if self._capture is not None:
                self._capture.cancel()
            if self._progress_id:
                self.app.progress.finish(self._progress_id)
            self._release_capture_locked()
            if self.task is not None:
                future, self.task = self.task, None
                future.cancel()

    def close(self) -> None:
        """Cancel active capture and any older recorder still draining its tail."""
        with self.state.recording_lock:
            self.cancel()
            for future in list(self._pending_recorders):
                future.cancel()

    def finish(self) -> None:
        """Close this capture's input while its private queue drains."""
        with self.state.recording_lock:
            if not self.is_recording or self.state.recording_owner is not self:
                return
            self.app.mark_user_activity()
            if self._progress_id and self.task is not None and not self.task.done():
                self.app.progress.begin(self._progress_id)
            self._capture.finish()
            self._release_capture_locked()

        # Restore unsuppressed lock keys.
        # Suppressed keys never reach the system, so their lock state does not need restoration.
        if self.shortcut.is_toggle_key() and not self.shortcut.suppress:
            self._restore_key()

    def _restore_key(self) -> None:
        """Restore key state; ShortcutManager filters the resulting synthetic events."""
        # Ask the manager to restore the key.
        # The manager sets the flag before generating input to prevent recapture.
        manager = self._manager_ref()
        if manager:
            logger.debug(Notice('diagnostic.task.restoring_key_state_suppress', value0=self.shortcut.key, value1=self.shortcut.suppress))
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(Notice('diagnostic.task.cannot_restore_manager_reference_lost', value0=self.shortcut.key))
