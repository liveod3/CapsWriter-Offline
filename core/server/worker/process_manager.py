# coding: utf-8
"""
Recognition process manager (ProcessManager).

Manage startup, model-loading supervision, and unexpected process exit.
"""
from __future__ import annotations

from core.i18n import Notice, tr

import sys
import os
import queue
import threading
import time
from collections import deque
from multiprocessing import Process, Manager, Event, Value
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING
from rich.panel import Panel
from config_server import ServerConfig as Config
from ..state import console
from .check_model import check_model
from . import logger
from ..delivery import ResultDeliveryError, SCHEDULING_RESUME_GRACE, positive_timeout
from .supervision import progress
from core.tools.daemon_executor import SimpleDaemonExecutor
from core.worker_bootstrap import configuration_snapshot, start_configured_worker
if TYPE_CHECKING:
    from ..app import CapsWriterServer


class ProcessManager:
    """
    Recognition process manager.
    
    Provide process-level control for CapsWriterServer.
    """
    def __init__(self, app: CapsWriterServer):
        self._process = None
        self._align_process = None
        self._align_lock = threading.Lock()
        self._align_monitor_thread = None
        self._monitor_stop = threading.Event()
        self._aligner_idle_exits = deque()
        self._last_aligner_churn_warning = 0.0
        self._manager = None
        self._runtime_last_check = time.monotonic()
        self._runtime_last_progress = None
        self._runtime_resume_deadline = None
        self.app = app
        self.is_alive = False
        import config_client
        import config_server
        # Preserve the local UI-language fallback; server logging uses ServerConfig only.
        self._child_config = configuration_snapshot(config_server, config_client)
        from core.i18n import get_language
        self._ui_language = Value('i', int(get_language() == 'zh-CN'))

    def publish_ui_language(self, language):
        """Update display language without restarting or interrupting inference."""
        self._ui_language.value = int(language == 'zh-CN')

    def start(self):
        """
        Start recognition and wait for model readiness.
        
        Returns:
            Process: Started subprocess.
        """
        # Ignore repeated activation.
        if self.is_alive: return
        self.is_alive = True
        self._monitor_stop.clear()

        # 1. Check prerequisites.
        check_model(interactive=False)

        # 2. Initialize shared resources.
        # Track active connections through a Manager list.
        state = self.app.state
        self._manager = Manager()
        state.sockets_id = self._manager.list()
        state.worker_failed = Event()
        state.worker_progress = Value('d', time.monotonic())
        
        # Capture stdin's file descriptor for Windows signal handling.
        stdin_fn = sys.stdin.fileno()
        
        # 3. Start the unloaded aligner sibling before the first file request.
        self._start_aligner_process()

        # 4. Create and start ASR.
        self._process = Process(
            target=start_configured_worker,
            args=(self._child_config, 'asr', state.queue_in,
                  state.queue_out,
                  state.sockets_id,
                  state.align_queue_in,
                  state.align_queue_out,
                  stdin_fn,
                  state.worker_failed,
                  state.worker_progress),
            kwargs={'ui_language': self._ui_language},
            daemon=True
        )
        self._process.start()
        
        # Publish the process in shared state.
        state.recognize_process = self._process
        logger.info(Notice('diagnostic.process_manager.recognition_process_started_pid', value0=self._process.pid))

        # 5. Poll for model readiness.
        self._wait_for_models()

        # Monitor ASR progress and replace only clean idle aligner exits.
        requested = getattr(self.app, '_stop_requested', None)
        if self.is_alive and not (requested is not None and requested.is_set()):
            self._align_monitor_thread = threading.Thread(
                target=self._monitor_aligner,
                name='aligner-process-monitor',
                daemon=True,
            )
            self._align_monitor_thread.start()
        
        return self._process

    def _start_aligner_process(self):
        """Ensure an aligner process is available to wait for requests."""
        with self._align_lock:
            if not self.is_alive:
                return None
            if self._align_process and self._align_process.is_alive():
                return self._align_process

            old_process = self._align_process
            if old_process is not None:
                try:
                    old_process.join(timeout=0)
                    old_process.close()
                except (OSError, ValueError):
                    pass

            state = self.app.state
            self._align_process = Process(
                target=start_configured_worker,
                args=(self._child_config, 'aligner', state.align_queue_in, state.align_queue_out),
                kwargs={'ui_language': self._ui_language},
                daemon=True,
            )
            self._align_process.start()
            if not self.is_alive:
                self._align_process.terminate()
                self._align_process.join(timeout=1)
                return None
            state.aligner_process = self._align_process
            logger.info(Notice('diagnostic.process_manager.aligner_process_started_pid', value0=self._align_process.pid))
            return self._align_process

    def _monitor_aligner(self):
        """Replace clean idle exits only; stop the service on death or lost progress."""
        while not self._monitor_stop.wait(0.5):
            if not self.is_alive:
                return
            try:
                self._check_runtime()
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ResultDeliveryError) else type(exc).__name__
                logger.error(Notice('diagnostic.process_manager.worker_supervision_stopped_service'), reason)
                self.app.state.worker_failed.set()
                return

    def _check_runtime(self):
        state = self.app.state
        if state.worker_failed.is_set():
            raise ResultDeliveryError('WorkerChannelFailed')
        if self._process is not None and not self._process.is_alive():
            raise ResultDeliveryError('WorkerExited')
        timeout = positive_timeout(Config, 'worker_stall_timeout', 600.0)
        heartbeat = progress(state.worker_progress)
        now = time.monotonic()
        gap = now - self._runtime_last_check
        self._runtime_last_check = now
        if heartbeat != self._runtime_last_progress:
            self._runtime_last_progress = heartbeat
            self._runtime_resume_deadline = None
        grace = min(timeout, SCHEDULING_RESUME_GRACE)
        stalled = now - heartbeat >= timeout
        if stalled and gap >= grace and self._runtime_resume_deadline is None:
            # Do not overwrite the worker's heartbeat or grant repeated extensions
            # without actual progress. Death/failure checks above remain immediate.
            self._runtime_resume_deadline = now + grace
            logger.info(Notice('diagnostic.process_manager.worker_resume_grace'), gap, grace)
        if (stalled
                and (self._runtime_resume_deadline is None or now >= self._runtime_resume_deadline)):
            raise ResultDeliveryError('WorkerStalled')
        with self._align_lock:
            process = self._align_process
            if process is not None and not process.is_alive():
                if process.exitcode not in (0, None):
                    raise ResultDeliveryError('AlignerExited')
                else:
                    logger.info(Notice('diagnostic.process_manager.replacing_an_idle_aligner_process'))
                    self._record_aligner_idle_exit()
            else:
                return
        self._start_aligner_process()

    def _record_aligner_idle_exit(self):
        """Detect repeated aligner unload/reload cycles within a short interval."""
        now = time.monotonic()
        window = 60.0
        self._aligner_idle_exits.append(now)
        while self._aligner_idle_exits and now - self._aligner_idle_exits[0] > window:
            self._aligner_idle_exits.popleft()

        if len(self._aligner_idle_exits) < 3:
            return
        if (self._last_aligner_churn_warning
                and now - self._last_aligner_churn_warning < 300):
            return

        self._last_aligner_churn_warning = now
        timeout = getattr(Config, 'aligner_idle_timeout', 600)
        logger.warning(
            Notice('diagnostic.process_manager.aligner_exited_times_within_seconds_aligner_idle_timeout', value0=len(self._aligner_idle_exits), value1=timeout)
        )
        console.print(Panel.fit(
            tr('gpu.churn', value0=len(self._aligner_idle_exits), value1=timeout),
            title=tr('gpu.churn_title'),
            border_style='bold yellow',
        ))

    def _wait_for_models(self):
        """Own one daemon read and bound startup even when a pipe read is wedged."""
        logger.info(Notice('diagnostic.process_manager.waiting_for_recognition_models'))
        deadline = time.monotonic() + positive_timeout(Config, 'model_startup_timeout', 300.0)
        read = None
        executor = SimpleDaemonExecutor()
        while self.is_alive:
            requested = getattr(self.app, '_stop_requested', None)
            if requested is not None and requested.is_set():
                self.app.stop()
                return
            failure = getattr(self.app.state, 'worker_failed', None)
            if failure is not None and failure.is_set():
                logger.error(Notice('diagnostic.process_manager.recognition_worker_failed_during_startup_stopping_server'))
                self.app.stop()
                return
            if time.monotonic() >= deadline:
                logger.error(Notice('diagnostic.process_manager.model_startup_timed_out_restart_required'))
                self.app.stop()
                return
            if self._process and not self._process.is_alive():
                self._handle_unexpected_exit()
                return
            try:
                if read is None:
                    read = executor.submit(self.app.state.queue_out.get, timeout=0.1)
                status = read.result(timeout=0.1)
                read = None
                if status is True:
                    progress(getattr(self.app.state, 'worker_progress', None), update=True)
                    break
                raise ResultDeliveryError('InvalidStartupResult')
            except FutureTimeout:
                if read.done():
                    logger.error(Notice('diagnostic.process_manager.model_startup_reader_failed_restart_required'))
                    self.app.stop()
                    return
                continue
            except queue.Empty:
                read = None
            except Exception as exc:
                logger.error(Notice('diagnostic.process_manager.model_startup_channel_failed'), type(exc).__name__)
                self.app.stop()
                return
            
        if not self.is_alive: return
        logger.info(Notice('diagnostic.process_manager.models_loaded_asr_service_ready'))
        console.rule(tr('server.ready'))
        console.line()

    def _handle_unexpected_exit(self):
        """Handle unexpected exit during model loading."""
        exit_code = self._process.exitcode
        if exit_code != 0:
            logger.error(Notice('diagnostic.process_manager.recognition_process_exited_unexpectedly_exitcode', value0=exit_code))
            logger.error(Notice('diagnostic.process_manager.possible_causes_include_damaged_models_native_library_conflicts'))
        
        # Request synchronous application shutdown.
        self.app.stop()

    def stop(self):
        """Stop subprocesses."""

        # Ignore repeated activation.
        if not self.is_alive: return
        self.is_alive = False
        self._monitor_stop.set()

        if self._align_monitor_thread and self._align_monitor_thread.is_alive():
            self._align_monitor_thread.join(timeout=1)

        for process, channel in (
                (self._align_process, getattr(self.app.state, 'align_queue_in', None)),
                (self._process, getattr(self.app.state, 'queue_in', None))):
            if process is None:
                continue
            try:
                self._stop_process(process, channel)
            except Exception as exc:
                logger.error(Notice('diagnostic.process_manager.process_cleanup_failed'), type(exc).__name__)

        if self._manager is not None:
            try:
                self._manager.shutdown()
            except Exception as exc:
                logger.error(Notice('diagnostic.process_manager.shared_registry_cleanup_failed'), type(exc).__name__)
            finally:
                self._manager = None
        for name in ('queue_in', 'queue_out', 'align_queue_in', 'align_queue_out'):
            channel = getattr(self.app.state, name, None)
            if channel is not None and hasattr(channel, 'cancel_join_thread'):
                # Never join a feeder writing to a child that was terminated.
                try:
                    channel.cancel_join_thread()
                    channel.close()
                except (OSError, ValueError) as exc:
                    logger.debug(Notice('diagnostic.process_manager.queue_cleanup_failed'), type(exc).__name__)

    @staticmethod
    def _stop_process(process, channel):
        if process.is_alive():
            try:
                channel.put(None, timeout=0.5)
            except (queue.Full, OSError, EOFError, ValueError) as exc:
                logger.debug(Notice('diagnostic.process_manager.worker_shutdown_signal_unavailable'), type(exc).__name__)
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
        else:
            process.join(timeout=0)
        if process.is_alive():
            logger.error(Notice('diagnostic.process_manager.worker_did_not_exit_after_termination'))
