# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态
"""

from __future__ import annotations
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
 
if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.audio.recorder import AudioRecorder
    from core.client.app import CapsWriterClient



class ShortcutTask:
    """
    单个快捷键的录音任务

    跟踪每个快捷键独立的录音状态，防止互相干扰。
    """

    AUDIO_READY_TIMEOUT = 5.0
    MAX_PENDING_RECORDERS = 8

    def __init__(self, app: CapsWriterClient, shortcut: Shortcut, recorder_class=None):
        """
        初始化快捷键任务

        Args:
            app: 客户端 App 实例
            shortcut: 快捷键配置
            recorder_class: AudioRecorder 类（可选，用于延迟导入）
        """
        self.app = app
        self.shortcut = shortcut
        self._recorder_class = recorder_class

        # 任务状态
        self.task: Optional[Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False
        self._launch_generation: int = 0
        self._progress_id: str | None = None
        self._capture = None
        self._pending_recorders = set()
        self._ready_cancel = Event()

        # hold_mode 状态跟踪
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()

        # 线程池（用于 countdown）
        self.pool = None

        # 录音状态动画
        self._status = Status('开始录音', spinner='point')

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _get_recorder(self) -> AudioRecorder:
        """获取 AudioRecorder 实例"""
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
            logger.warning('Microphone readiness timed out; capture released')
            show_status_hint('麦克风准备超时，请重试', duration_ms=2200, dot_color='#EF4444')

    def launch(self) -> bool:
        """Atomically claim the microphone across all shortcut tasks."""
        self.app.mark_user_activity()
        with self.state.recording_lock:
            if getattr(self.app, '_stopping', False) or self.state.recording_owner is not None:
                return False
            if max(len(self.state.recording_futures), len(self.state.recording_tasks)) >= self.MAX_PENDING_RECORDERS:
                show_status_hint('Previous recordings are still sending. Please wait.',
                                 duration_ms=2200)
                return False
            return self._launch_locked()

    def _launch_locked(self) -> bool:
        self._launch_generation += 1
        generation = self._launch_generation

        if getattr(self.state, 'dictation_manually_paused', False):
            show_status_hint('Dictation is paused. Resume from the tray menu.', duration_ms=2000)
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
            self.task.add_done_callback(
                lambda future: self._recorder_done(future, capture, recorder.task_id)
            )
        except Exception as exc:
            if capture is not None and self._capture is capture:
                self.cancel()
            logger.error(f'Could not start recording: {type(exc).__name__}')
            return False

        if not self.is_recording:
            return False
        # stream.start() 后硬件可能仍在唤醒；只在首个音频块到达后提示可以说话。
        ready_event = self.app.stream.get_ready_event()
        if self.app.stream.is_ready(ready_event):
            self._show_recording_ready(generation, clear_preparing_hint=False)
        else:
            message = '正在准备麦克风，请稍候'
            logger.info(f"[{self.shortcut.key}] {message}")
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
            if self.task is future:
                self.task = None
            if self._capture is capture and self.is_recording:
                capture.cancel()
                self._release_capture_locked()
                self.app.progress.finish(progress_id)
            if error is not None:
                logger.error(f'Recording worker failed: {type(error).__name__}')
                if self.state.recording_owner is None and not getattr(self.app, '_stopping', False):
                    message = ('Recording stopped: audio buffer is full. Please retry.'
                               if str(error) == 'CaptureBufferOverflow' else
                               'Recording failed. Please retry.')
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

        # 执行 restore（可恢复按键 + 非阻塞模式）
        # 阻塞模式下按键不会发送到系统，状态不会改变，不需要恢复
        if self.shortcut.is_toggle_key() and not self.shortcut.suppress:
            self._restore_key()

    def _restore_key(self) -> None:
        """恢复按键状态（防自捕获逻辑由 ShortcutManager 处理）"""
        # 通知管理器执行 restore
        # 防自捕获：管理器会设置 flag 再发送按键
        manager = self._manager_ref()
        if manager:
            logger.debug(f"[{self.shortcut.key}] 自动恢复按键状态 (suppress={self.shortcut.suppress})")
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(f"[{self.shortcut.key}] manager 引用丢失，无法 restore")
