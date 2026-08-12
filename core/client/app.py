# coding: utf-8
"""
CapsWriter Offline 客户端主程序门面类 (Facade)

采用外观模式统一管理音频流 (AudioStreamManager)、
识别结果处理 (ResultProcessor) 和快捷键管理 (ShortcutManager)。
"""

import os
import sys
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
    TrayManager,
    MicRunner, FileRunner
)
from .audio.stream import AudioStreamManager
from .shortcut.shortcut_manager import ShortcutManager
from .shortcut.shortcut_config import Shortcut

from .udp.udp_control import UDPController

from .hotword.manager import HotwordManager
from .llm.llm_handler import LLMHandler
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
    def __init__(self):
        # 确保正确的工作目录
        self.base_dir = Path(__file__).parents[2]
        os.chdir(self.base_dir)
            
        # 初始化事件循环
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
            
        # 初始化状态容器
        self.state = ClientState(app=self)

        # 初始化热词管理器
        self.hotword = HotwordManager(
            hotword_files=None,
            threshold=Config.hot_thresh,
            similar_threshold=Config.hot_similar
        )

        # 4. 初始化 LLM 润色系统
        self.llm = LLMHandler(app=self)
        
        self.output = TextOutput()
        self.diary = DiaryWriter(base_path=self.base_dir)

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
        self._idle_suspend_thread = None

    def _idle_suspend_loop(self) -> None:
        """闲置检测循环：超过阈值后自动挂起听写。"""
        while self._idle_suspend_running:
            time.sleep(1.0)

            if not Config.enable_idle_suspend:
                continue
            if Config.idle_suspend_seconds <= 0:
                continue
            if self.state.dictation_paused or self.state.recording:
                continue

            idle_for = time.time() - self.state.last_activity_time
            if idle_for < Config.idle_suspend_seconds:
                continue

            paused = self.pause_dictation(show_hint=False)
            if paused:
                message = '听写已闲置挂起：麦克风已释放'
                logger.info(message)
                console.print(f'\n[bold yellow]● {message}[/]')
                show_status_hint('听写已闲置挂起', duration_ms=1800, dot_color='#F59E0B')
                self.state.last_activity_time = time.time()

    def pause_dictation(self, show_hint: bool = True) -> bool:
        """暂停听写并释放麦克风流，避免耳机长期进入通话模式。"""
        if self.state.recording:
            if show_hint:
                message = '当前正在录音，稍后再暂停'
                logger.info(message)
                console.print(f'\n[bold yellow]● {message}[/]')
                show_status_hint(message, duration_ms=1600, dot_color='#F59E0B')
            return False

        if self.state.dictation_paused:
            return True

        self.state.dictation_paused = True
        # 先发布挂起状态，再释放录音流并保留只读设备监控，避免监控线程误重开麦克风。
        self.stream.stop(keep_monitor=True)
        set_dictation_paused(True)
        logger.info("听写已暂停：音频流已释放")

        if show_hint:
            console.print('\n[cyan]● 听写已暂停：麦克风已释放[/]')
            show_status_hint('听写已暂停', duration_ms=1400, dot_color='#7DD3FC')
        return True

    def resume_dictation(self, show_hint: bool = True, silent_stream: bool = True) -> bool:
        """恢复听写并重新打开麦克风流。"""
        if not self.state.dictation_paused:
            return True

        stream = self.stream.start(silent=silent_stream, force=True)
        if stream is None:
            logger.warning("恢复听写失败：音频流启动失败")
            if show_hint:
                show_status_hint('恢复听写失败：无法打开麦克风', duration_ms=2000, dot_color='#EF4444')
            return False

        self.state.dictation_paused = False
        set_dictation_paused(False)
        logger.info("听写恢复流程已启动：音频流已重新打开，等待设备就绪")
        self.mark_user_activity()

        if show_hint:
            ready_event = self.stream.get_ready_event()
            if self.stream.is_ready(ready_event):
                message = '听写已恢复：麦克风已就绪'
                logger.info(message)
                console.print(f'\n[bold green]● {message}[/]')
                show_status_hint('听写已恢复', duration_ms=1200, dot_color='#34D399')
            else:
                message = '正在准备麦克风，请稍候'
                logger.info(message)
                console.print(f'\n[bold yellow]● {message}[/]')
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
        if not ready_event.wait(timeout=5.0):
            if not self.state.dictation_paused and ready_event is self.stream.get_ready_event():
                message = '麦克风准备超时，请重试'
                logger.info(message)
                console.print(f'\n[bold red]● {message}[/]')
                show_status_hint(message, duration_ms=2200, dot_color='#EF4444')
            return

        if not self.state.dictation_paused and self.stream.is_ready(ready_event):
            message = '听写已恢复：麦克风已就绪'
            logger.info(message)
            console.print(f'\n[bold green]● {message}[/]')
            show_status_hint('听写已恢复', duration_ms=1200, dot_color='#34D399')

    def toggle_dictation_pause(self) -> bool:
        """切换听写暂停状态。"""
        if self.state.dictation_paused:
            return self.resume_dictation(show_hint=True, silent_stream=False)
        return self.pause_dictation(show_hint=True)

    def stop(self):
        """
        统一释放所有资源（清理顺序：硬件 -> 托盘 -> WebSocket -> State）
        """
        if self._stopping:
            return
        self._stopping = True

        logger.info("正在执行 CapsWriterClient 资源释放...")

        # 先终止结果处理循环，防止关闭当前连接后触发自动重连。
        processor = getattr(self._active_runner, 'processor', None)
        if processor is not None:
            processor.request_exit()

        # 1. 停止核心运行组件
        self.stop_idle_suspend_monitor()
        self.udp.stop()
        self.shortcut.stop()
        self.stream.stop()

        # 2. 托盘资源
        self.tray.stop()

        # 3. 关闭监控
        self.hotword.stop()
        self.llm.stop()

        # 4. 关闭 WebSocket 连接
        self.ws.close_sync()

        # 5. 重置 State
        try:
            self.state.reset()
        except Exception as e:
            logger.warning(f"重置状态时发生错误: {e}")

        # 麦克风模式由 ResultProcessor 在关闭连接后自然返回，让关闭握手有
        # 机会完成；其他模式仍沿用主动停止事件循环的退出方式。
        if processor is None:
            self.loop.stop()

        logger.info("资源释放完成")
        console.print('[green4]再见！')


    def start(self):
        """
        启动客户端 (唯一入口)
        
        自动根据命令行参数识别模式。内部管理异步循环。
        """

        # 注册退出函数
        register_signal(self.stop)

        files = [Path(f) for f in sys.argv[1:] if os.path.exists(f)]

        if files:
            # 文件转录模式
            runner = FileRunner(self, files)
        else:
            # 麦克风实时模式
            runner = MicRunner(self)
        self._active_runner = runner
        
        try:
            self.loop.run_until_complete(runner.run())
        except RuntimeError:
            ...
