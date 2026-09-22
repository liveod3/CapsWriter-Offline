# coding: utf-8
"""
CapsWriter Offline 客户端主程序门面类 (Facade)

采用外观模式统一管理音频流 (AudioStreamManager)、
识别结果处理 (ResultProcessor) 和快捷键管理 (ShortcutManager)。
"""

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
from core.tools.empty_working_set import empty_current_working_set
from platform import system
from core.ui import set_dictation_paused, show_status_hint



class CapsWriterClient:
    """
    CapsWriter 客户端门面类
    
    管理的外部接口简洁：start()。
    """
    def __init__(self, command: ClientCommand):
        self.command = command

        # 确保正确的工作目录
        self.base_dir = Path(__file__).parents[2]
        os.chdir(self.base_dir)
            
        # 初始化事件循环
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
            
        # 初始化状态容器
        self.state = ClientState(app=self)

        self.llm = TextActionService(Config, self.base_dir, status_callback=show_status_hint)
        from core.client.processing_status import ProcessingStatus
        from core.ui.recording_indicator import set_processing_status
        self.progress = ProcessingStatus(set_processing_status)
        self.caret_context = CaretContextCapture(Config, self.base_dir)
        
        self.output = TextOutput()
        self.diary = DiaryWriter(base_path=self.base_dir / getattr(Config, 'transcript_dir', 'logs/transcripts'))
        self.action_records = DiaryWriter(base_path=self.base_dir / 'logs' / 'text-actions')

        # 初始化各管理器
        self.ws = WebSocketManager(self)
        self.tray = TrayManager(self)

        # 实例化硬件资源管理组件
        self.stream = AudioStreamManager(self)
        self.shortcut = ShortcutManager(self, [Shortcut(**sc) for sc in Config.shortcuts])
        self.udp = UDPController(self.shortcut)

        # 内存清理
        empty_current_working_set()

        # 闲置自动挂起监控
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
        logger.info(message, extra={'console_handled': True})
        console.print(message, markup=False)

    def apply_config_reload(self):
        """Publish only after capture, upload, LLM, output and archives all settle."""
        with self.state.recording_lock:
            if (self._stopping or self._file_active or self.state.recording_owner is not None
                    or self.state.recording_futures or self.state.recording_tasks
                    or self.state.dictation_uploads or self.state.task_contexts):
                return
            changed = self.config_reload.apply()
            if 'transcript_dir' in changed:
                self.diary.base_path = self.base_dir / Config.transcript_dir
        if changed:
            if Config.llm_enabled:
                try:
                    self.llm.start()
                except Exception as exc:
                    self._report_config('LLM cancel key unavailable: ' + type(exc).__name__)
            self._report_config('Configuration applied: ' + ', '.join(changed))

    def mark_user_activity(self) -> None:
        """标记用户活跃时间，用于闲置自动挂起判断。"""
        self.state.last_activity_time = time.time()

    def start_idle_suspend_monitor(self) -> None:
        """启动闲置自动挂起监控线程。"""
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
        logger.info(f"闲置自动挂起已启用: {Config.idle_suspend_seconds}s")

    def stop_idle_suspend_monitor(self) -> None:
        """停止闲置自动挂起监控线程。"""
        self._idle_suspend_running = False
        self._idle_stop.set()
        thread = self._idle_suspend_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
            if not thread.is_alive():
                self._idle_suspend_thread = None

    def _idle_suspend_loop(self) -> None:
        """闲置检测循环：超过阈值后自动挂起听写。"""
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
                message = '听写已闲置挂起：麦克风已释放'
                logger.info(message)
                console.print(f'\n[ui.warning]●[/] [ui.value]{message}[/]')
                show_status_hint('听写已闲置挂起', duration_ms=1800, dot_color='#F59E0B')
                self.state.last_activity_time = time.time()

    def pause_dictation(self, show_hint: bool = True, *, manual: bool = True) -> bool:
        """Serialize pause with shortcut capture ownership changes."""
        with self._dictation_control_lock:
            return self._pause_dictation_locked(show_hint, manual=manual)

    def _pause_dictation_locked(self, show_hint: bool = True, *, manual: bool = True) -> bool:
        """暂停听写并释放麦克风流，避免耳机长期进入通话模式。"""
        with self.state.recording_lock:
            if self._stopping:
                return False
            if self.state.recording:
                if show_hint:
                    show_status_hint('当前正在录音，稍后再暂停', duration_ms=1600, dot_color='#F59E0B')
                return False
            if manual:
                self.state.dictation_manually_paused = True
            if self.state.dictation_paused:
                return True
            self.state.dictation_paused = True
        # 先发布挂起状态，再释放录音流并保留只读设备监控，避免监控线程误重开麦克风。
        self.stream.stop(keep_monitor=True)
        set_dictation_paused(True)
        logger.info("听写已暂停：音频流已释放")

        if show_hint:
            console.print('\n[ui.accent]●[/] [ui.value]听写已暂停，麦克风已释放[/]')
            show_status_hint('听写已暂停', duration_ms=1400, dot_color='#7DD3FC')
        return True

    def resume_dictation(self, show_hint: bool = True, silent_stream: bool = True) -> bool:
        """Serialize resume with pause and shortcut ownership changes."""
        with self._dictation_control_lock:
            return self._resume_dictation_locked(show_hint, silent_stream)

    def _resume_dictation_locked(self, show_hint: bool = True, silent_stream: bool = True) -> bool:
        """恢复听写并重新打开麦克风流。"""
        with self.state.recording_lock:
            if self._stopping:
                return False
            if not self.state.dictation_paused:
                return True

        stream = self.stream.start(silent=silent_stream, force=True)
        if stream is None:
            logger.warning("恢复听写失败：音频流启动失败")
            if show_hint:
                show_status_hint('恢复听写失败：无法打开麦克风', duration_ms=2000, dot_color='#EF4444')
            return False

        with self.state.recording_lock:
            if self._stopping:
                return False
            self.state.dictation_paused = False
            self.state.dictation_manually_paused = False
        set_dictation_paused(False)
        logger.info("听写恢复流程已启动：音频流已重新打开，等待设备就绪")
        self.mark_user_activity()

        if show_hint:
            ready_event = self.stream.get_ready_event()
            if self.stream.is_ready(ready_event):
                message = '听写已恢复：麦克风已就绪'
                logger.info(message)
                console.print(f'\n[ui.success]●[/] [ui.value]{message}[/]')
                show_status_hint('听写已恢复', duration_ms=1200, dot_color='#34D399')
            else:
                message = '正在准备麦克风，请稍候'
                logger.info(message)
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
        """托盘恢复时，等设备真正交付音频后再提示恢复完成。"""
        deadline = time.monotonic() + 5.0
        while not self._stopping and not ready_event.is_set() and time.monotonic() < deadline:
            if self._idle_stop.wait(0.05):
                return
        if self._stopping:
            return
        if not ready_event.is_set():
            if not self.state.dictation_paused and ready_event is self.stream.get_ready_event():
                message = '麦克风准备超时，请重试'
                logger.info(message)
                console.print(f'\n[ui.error]●[/] [ui.value]{message}[/]')
                show_status_hint(message, duration_ms=2200, dot_color='#EF4444')
            return

        if not self.state.dictation_paused and self.stream.is_ready(ready_event):
            message = '听写已恢复：麦克风已就绪'
            logger.info(message)
            console.print(f'\n[ui.success]●[/] [ui.value]{message}[/]')
            show_status_hint('听写已恢复', duration_ms=1200, dot_color='#34D399')

    def toggle_dictation_pause(self) -> bool:
        """切换听写暂停状态。"""
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
                logger.warning('Shutdown operation failed: %s', type(exc).__name__)

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
            logger.warning('Connection close failed: %s', type(exc).__name__)
        if self._runner_task is not None and not self._runner_task.done():
            self._runner_task.cancel()
            await asyncio.gather(self._runner_task, return_exceptions=True)
        await release(self.state.reset)
        logger.info('Client resource cleanup complete')


    def start(self) -> int:
        """
        启动客户端 (唯一入口)
        
        根据已解析的命令选择运行器，并管理异步循环。
        """

        # 注册退出函数
        register_signal(self.stop)

        if self.command.mode is ClientMode.MIC:
            runner = MicRunner(self)
        elif self.command.mode is ClientMode.TRANSCRIBE:
            files = resolve_input_paths(
                list(self.command.inputs),
                recursive=self.command.recursive,
            )
            if not files:
                console.print('[ui.error]✗ 没有发现可转写的媒体文件[/]')
                logger.error('没有发现可转写的媒体文件')
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
