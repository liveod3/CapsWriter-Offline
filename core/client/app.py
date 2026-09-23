# coding: utf-8
"""
CapsWriter client facade.

Coordinate AudioStreamManager,
ResultProcessor, and ShortcutManager.
"""

from core.i18n import Notice, tr

import os
import asyncio
import threading
import time
from pathlib import Path

from .state import ClientState
from . import logger
from config_client import ClientConfig as Config, __version__
from core.tools.signal_handler import register_signal
from .state import console
from .connection import WebSocketManager
from typing import TYPE_CHECKING, Optional
from .manager import (
    TrayManager, MicRunner, FileRunner, SrtRebuildRunner
)
from .manager.file_runner import resolve_input_paths
from .cli import ClientCommand, ClientMode
from .audio.stream import AudioStreamManager
from .shortcut.shortcut_manager import ShortcutManager
from .shortcut.shortcut_config import Shortcut

from .udp.udp_control import UDPController

from .llm.service import TextActionService
from .caret_context import CaretContextCapture
from .output.text_output import TextOutput
from .diary.diary_writer import DiaryWriter
from core.diagnostics import storage_path
from core.tools.empty_working_set import empty_current_working_set
from platform import system
from core.ui import set_dictation_paused, show_status_hint



class CapsWriterClient:
    """
    CapsWriter client facade.
    
    Expose start() as the main entry point.
    """
    def __init__(self, command: ClientCommand):
        from core.i18n import set_language
        set_language(getattr(Config, 'ui_language', 'auto'))
        self.command = command

        # Set the working directory.
        self.base_dir = Path(__file__).parents[2]
        os.chdir(self.base_dir)
            
        # Initialize the event loop.
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
            
        # Initialize shared state.
        self.state = ClientState(app=self)

        self.llm = TextActionService(Config, self.base_dir, status_callback=show_status_hint)
        from core.client.processing_status import ProcessingStatus
        from core.ui.recording_indicator import set_processing_status
        self.progress = ProcessingStatus(set_processing_status)
        self.caret_context = CaretContextCapture(Config, self.base_dir)
        
        self.output = TextOutput()
        self.diary = DiaryWriter(base_path=storage_path(self.base_dir, getattr(Config, 'transcript_dir', 'records/transcripts')))

        # Initialize managers.
        self.ws = WebSocketManager(self)
        self.tray = TrayManager(self)

        # Create hardware resource managers.
        self.stream = AudioStreamManager(self)
        self.shortcut = ShortcutManager(self, [Shortcut(**sc) for sc in Config.shortcuts])
        self.udp = UDPController(self.shortcut)

        # Release unused working-set memory.
        empty_current_working_set()

        # Monitor idle suspension.
        self._idle_suspend_running = False
        self._idle_suspend_thread = None
        self._active_runner = None
        self._stopping = False
        self._shutdown_lock = threading.Lock()
        self._dictation_control_lock = threading.RLock()
        self._shutdown_future = None
        self._runner_task = None
        self._idle_stop = threading.Event()
        self._file_active = command.mode is not ClientMode.MIC
        import config_client
        from config_templates import config_client_template
        from core.config_reload import ConfigReloader, CLIENT_LIVE
        self.config_reload = ConfigReloader(
            self.base_dir / 'config_client.py', config_client, config_client_template,
            'ClientConfig', CLIENT_LIVE, self._report_config,
        )

    def _report_config(self, message):
        from core.i18n import localize_notice
        logger.info(message, extra={'console_handled': True})
        console.print(localize_notice(message), markup=False)

    def apply_config_reload(self):
        """Publish only after capture, upload, LLM, output and archives all settle."""
        with self.state.recording_lock:
            if (self._stopping or self._file_active or self.state.recording_owner is not None
                    or self.state.recording_futures or self.state.recording_tasks
                    or self.state.dictation_uploads or self.state.task_contexts):
                return
            changed = self.config_reload.apply()
            if 'ui_language' in changed:
                from core.i18n import set_language
                set_language(Config.ui_language)
            if 'transcript_dir' in changed:
                self.diary.base_path = storage_path(self.base_dir, Config.transcript_dir)
        if changed:
            if 'ui_language' in changed:
                from core.ui.tray import refresh_language
                refresh_language()
            if Config.llm_enabled:
                try:
                    self.llm.start()
                except Exception as exc:
                    self._report_config('LLM cancel key unavailable: ' + type(exc).__name__)
            from core.i18n import Notice
            self._report_config(Notice('config.applied', fields=', '.join(changed)))

    def mark_user_activity(self) -> None:
        """Record user activity for idle suspension."""
        self.state.last_activity_time = time.time()

    def start_idle_suspend_monitor(self) -> None:
        """Start the idle suspension monitor."""
        if not Config.enable_idle_suspend:
            return
        if self._idle_suspend_running:
            return

        self._idle_suspend_running = True
        self._idle_stop.clear()
        self._idle_suspend_thread = threading.Thread(
            target=self._idle_suspend_loop,
            daemon=True,
            name='IdleSuspendMonitor'
        )
        self._idle_suspend_thread.start()
        logger.info(Notice('diagnostic.app.idle_suspension_enabled_s', value0=Config.idle_suspend_seconds))

    def stop_idle_suspend_monitor(self) -> None:
        """Stop the idle suspension monitor."""
        self._idle_suspend_running = False
        self._idle_stop.set()
        thread = self._idle_suspend_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
            if not thread.is_alive():
                self._idle_suspend_thread = None

    def _idle_suspend_loop(self) -> None:
        """Suspend dictation after the configured idle interval."""
        while self._idle_suspend_running:
            if self._idle_stop.wait(1.0) or self._stopping:
                break

            if not Config.enable_idle_suspend:
                continue
            if Config.idle_suspend_seconds <= 0:
                continue
            if self.state.dictation_paused or self.state.recording:
                continue

            idle_for = time.time() - self.state.last_activity_time
            if idle_for < Config.idle_suspend_seconds:
                continue

            paused = self.pause_dictation(show_hint=False, manual=False)
            if paused:
                message = tr('mic.idle_detail')
                logger.info(Notice('diagnostic.app.ui_status'), 'mic.idle_detail')
                console.print(f'\n[ui.warning]●[/] [ui.value]{message}[/]')
                show_status_hint(tr('mic.idle'), duration_ms=1800, dot_color='#F59E0B')
                self.state.last_activity_time = time.time()

    def pause_dictation(self, show_hint: bool = True, *, manual: bool = True) -> bool:
        """Serialize pause with shortcut capture ownership changes."""
        with self._dictation_control_lock:
            return self._pause_dictation_locked(show_hint, manual=manual)

    def _pause_dictation_locked(self, show_hint: bool = True, *, manual: bool = True) -> bool:
        """Pause dictation and release the microphone to leave headset call mode."""
        with self.state.recording_lock:
            if self._stopping:
                return False
            if self.state.recording:
                if show_hint:
                    show_status_hint(tr('mic.pause_busy'), duration_ms=1600, dot_color='#F59E0B')
                return False
            if manual:
                self.state.dictation_manually_paused = True
            if self.state.dictation_paused:
                return True
            self.state.dictation_paused = True
        # Publish suspension before releasing the stream so the monitor cannot reopen it.
        self.stream.stop(keep_monitor=True)
        set_dictation_paused(True)
        logger.info(Notice('diagnostic.app.dictation_paused_audio_stream_released'))

        if show_hint:
            console.print(tr('mic.pause_console'))
            show_status_hint(tr('mic.paused'), duration_ms=1400, dot_color='#7DD3FC')
        return True

    def resume_dictation(self, show_hint: bool = True, silent_stream: bool = True) -> bool:
        """Serialize resume with pause and shortcut ownership changes."""
        with self._dictation_control_lock:
            return self._resume_dictation_locked(show_hint, silent_stream)

    def _resume_dictation_locked(self, show_hint: bool = True, silent_stream: bool = True) -> bool:
        """Resume dictation and reopen the microphone stream."""
        with self.state.recording_lock:
            if self._stopping:
                return False
            if not self.state.dictation_paused:
                return True

        stream = self.stream.start(silent=silent_stream, force=True)
        if stream is None:
            logger.warning(Notice('diagnostic.app.cannot_resume_dictation_audio_stream_failed_to_start'))
            if show_hint:
                show_status_hint(tr('mic.resume_failed'), duration_ms=2000, dot_color='#EF4444')
            return False

        with self.state.recording_lock:
            if self._stopping:
                return False
            self.state.dictation_paused = False
            self.state.dictation_manually_paused = False
        set_dictation_paused(False)
        logger.info(Notice('diagnostic.app.dictation_resume_started_audio_stream_reopened_waiting_for'))
        self.mark_user_activity()

        if show_hint:
            ready_event = self.stream.get_ready_event()
            if self.stream.is_ready(ready_event):
                message = tr('mic.ready')
                logger.info(Notice('diagnostic.app.ui_status'), 'mic.ready')
                console.print(f'\n[ui.success]●[/] [ui.value]{message}[/]')
                show_status_hint(tr('mic.resumed'), duration_ms=1200, dot_color='#34D399')
            else:
                message = tr('mic.preparing')
                logger.info(Notice('diagnostic.app.ui_status'), 'mic.preparing')
                console.print(f'\n[ui.warning]●[/] [ui.value]{message}[/]')
                show_status_hint(message, duration_ms=5000, dot_color='#F59E0B')
                threading.Thread(
                    target=self._show_resume_hint_when_ready,
                    args=(ready_event,),
                    daemon=True,
                    name='dictation-ready-hint',
                ).start()
        return True

    def _show_resume_hint_when_ready(self, ready_event: threading.Event) -> None:
        """Wait for audio delivery before reporting that tray resume is complete."""
        deadline = time.monotonic() + 5.0
        while not self._stopping and not ready_event.is_set() and time.monotonic() < deadline:
            if self._idle_stop.wait(0.05):
                return
        if self._stopping:
            return
        if not ready_event.is_set():
            if not self.state.dictation_paused and ready_event is self.stream.get_ready_event():
                message = tr('mic.ready_timeout')
                logger.info(Notice('diagnostic.app.ui_status'), 'mic.ready_timeout')
                console.print(f'\n[ui.error]●[/] [ui.value]{message}[/]')
                show_status_hint(message, duration_ms=2200, dot_color='#EF4444')
            return

        if not self.state.dictation_paused and self.stream.is_ready(ready_event):
            message = tr('mic.ready')
            logger.info(Notice('diagnostic.app.ui_status'), 'mic.ready')
            console.print(f'\n[ui.success]●[/] [ui.value]{message}[/]')
            show_status_hint(tr('mic.resumed'), duration_ms=1200, dot_color='#34D399')

    def toggle_dictation_pause(self) -> bool:
        """Toggle dictation pause."""
        if self.state.dictation_paused:
            return self.resume_dictation(show_hint=True, silent_stream=False)
        return self.pause_dictation(show_hint=True)

    def stop(self):
        """Request shutdown from any thread without stopping the owning loop early."""
        with self._shutdown_lock:
            if self._stopping:
                return
            self._stopping = True
            self.stream.request_shutdown()
            self._idle_stop.set()
            if not self.loop.is_closed():
                self._shutdown_future = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)

    async def _shutdown(self):
        """Keep the event loop alive until recording and hardware cleanup finishes."""
        if hasattr(self, 'config_reload'):
            await self.config_reload.close()
        self.progress.close()
        processor = getattr(self._active_runner, 'processor', None)
        if processor is not None:
            processor.request_exit()
        self.ws.begin_shutdown()

        async def release(operation):
            try:
                await asyncio.to_thread(operation)
            except Exception as exc:
                logger.warning(Notice('diagnostic.app.shutdown_operation_failed'), type(exc).__name__)

        await release(self.stop_idle_suspend_monitor)
        await release(self.udp.stop)
        await release(self.shortcut.stop)
        await release(self.caret_context.close)
        await release(self.llm.stop)
        # Future.cancel() reports done before its asyncio coroutine finishes.
        # The separate task set includes file-writer and hardware-resume cleanup.
        await asyncio.sleep(0)
        recordings = list(self.state.recording_tasks)
        for recording in recordings:
            recording.cancel()
        if recordings:
            await asyncio.gather(*recordings, return_exceptions=True)
        await release(self.stream.close)
        await release(self.tray.stop)
        try:
            await self.ws.close()
        except Exception as exc:
            logger.warning(Notice('diagnostic.app.connection_close_failed'), type(exc).__name__)
        if self._runner_task is not None and not self._runner_task.done():
            self._runner_task.cancel()
            await asyncio.gather(self._runner_task, return_exceptions=True)
        await release(self.state.reset)
        logger.info(Notice('diagnostic.app.client_resource_cleanup_complete'))


    def start(self) -> int:
        """
        Start the client.
        
        Select the parsed command's runner and manage the event loop.
        """

        # Register shutdown cleanup.
        register_signal(self.stop)

        if self.command.mode is ClientMode.MIC:
            runner = MicRunner(self)
        elif self.command.mode is ClientMode.TRANSCRIBE:
            files = resolve_input_paths(
                list(self.command.inputs),
                recursive=self.command.recursive,
            )
            if not files:
                console.print(tr('file.no_media'))
                logger.error(Notice('diagnostic.app.no_transcribable_media_files_found'))
                return 2
            runner = FileRunner(
                self,
                files,
                output_formats=self.command.output_formats,
            )
        else:
            runner = SrtRebuildRunner(
                self.command.text_file,
                self.command.json_file,
            )
        self._active_runner = runner
        
        try:
            self.config_reload.task = self.loop.create_task(
                self.config_reload.watch(self.apply_config_reload))
            self._runner_task = self.loop.create_task(runner.run())
            succeeded = self.loop.run_until_complete(self._runner_task)
        except asyncio.CancelledError:
            if not self._stopping:
                raise
            return 0
        finally:
            self.stop()
            if self._shutdown_future is not None:
                self.loop.run_until_complete(asyncio.wrap_future(self._shutdown_future, loop=self.loop))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()
        return 0 if succeeded is not False else 1
