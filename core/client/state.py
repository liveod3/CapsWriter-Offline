# coding: utf-8
"""
客户端状态管理模块

提供 ClientState 类用于管理客户端的全局状态。
使用 dataclass 提供类型安全和清晰的状态定义。
"""

from __future__ import annotations

import asyncio
import time
from threading import RLock
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any

if TYPE_CHECKING:
    import sounddevice as sd
    from websockets.legacy.client import WebSocketClientProtocol
    from .app import CapsWriterClient

from rich.console import Console
from rich.theme import Theme

from . import logger


# 配置 Rich console
_theme = Theme({
    'ui.title': 'bold #F8FAFC',
    'ui.accent': 'bold #38BDF8',
    'ui.secondary': '#C4B5FD',
    'ui.label': '#93C5FD',
    'ui.value': '#F1F5F9',
    'ui.muted': '#CBD5E1',
    'ui.success': 'bold #5EE6A8',
    'ui.warning': 'bold #FFD166',
    'ui.error': 'bold #FF7B89',
    'ui.border': '#60A5FA',
    'ui.progress': '#38BDF8',
    'ui.progress.done': '#5EE6A8',
    'markdown.code': '#38BDF8',
    'markdown.item.number': '#FFD166',
    # 兼容尚未迁移的旧标记，避免同一客户端出现两套基础色。
    'green': '#5EE6A8',
    'green4': '#5EE6A8',
    'cyan': '#38BDF8',
    'yellow': '#FFD166',
    'red': '#FF7B89',
    'bright_red': '#FF7B89',
})
console = Console(highlight=False, soft_wrap=True, theme=_theme)


@dataclass
class ClientState:
    """
    客户端运行状态

    管理客户端运行过程中的所有共享状态，包括事件循环、消息队列、
    WebSocket 连接、音频流和录音状态等。

    Attributes:
        loop: asyncio 事件循环
        queue_in: 音频数据输入队列
        queue_out: 处理结果输出队列（保留）
        websocket: WebSocket 客户端连接
        stream: 音频输入流
        recording: 是否正在录音
        recording_start_time: 录音开始时间戳
        audio_files: 任务ID到音频文件路径的映射
        last_recognition_text: 最近一次识别的最终文本，用于保留 ASR 原文
    """

    queue_in: asyncio.Queue = field(default_factory=asyncio.Queue)
    queue_out: asyncio.Queue = field(default_factory=asyncio.Queue)
    websocket: Optional[WebSocketClientProtocol] = None
    stream: Optional[sd.InputStream] = None
    app: Optional[CapsWriterClient] = None

    recording: bool = False
    # Serialize shortcut ownership changes; callbacks use the capture snapshot.
    recording_lock: Any = field(default_factory=RLock, repr=False)
    recording_owner: Any = field(default=None, repr=False)
    capture: Any = field(default=None, repr=False)
    recording_futures: set = field(default_factory=set, repr=False)
    recording_tasks: set = field(default_factory=set, repr=False)
    recorder_by_id: dict = field(default_factory=dict, repr=False)
    recording_start_time: float = 0.0
    dictation_paused: bool = False
    dictation_manually_paused: bool = False
    task_contexts: dict = field(default_factory=dict)
    last_activity_time: float = field(default_factory=time.time)
    audio_files: Dict[str, Path] = field(default_factory=dict)

    # 最近一次识别结果（未经 LLM 处理）
    last_recognition_text: Optional[str] = None
    
    # 最近一次输出内容（如果是 LLM 润色，则是润色结果；否则是原始识别结果）
    last_output_text: Optional[str] = None
    

    
    def reset(self) -> None:
        """
        重置状态
        
        清理所有状态，关闭连接和流。用于重新初始化或退出时清理。
        """
        logger.debug("正在重置客户端状态...")
        
        # 关闭 WebSocket 连接
        ws = self.websocket
        if ws is not None:
            try:
                if not ws.closed and self.app and self.app.loop and self.app.loop.is_running():
                    asyncio.run_coroutine_threadsafe(ws.close(), self.app.loop)
            except Exception:
                pass
            self.websocket = None
        
        # 关闭音频流
        if self.stream is not None:
            try:
                self.stream.close()
                logger.debug("音频流已关闭")
            except Exception:
                pass
            self.stream = None
        
        # 重置其他状态
        with self.recording_lock:
            if self.capture is not None:
                self.capture.cancel()
            self.capture = None
            self.recording_owner = None
            self.recorder_by_id.clear()
            self.recording = False
            self.recording_start_time = 0.0
        self.dictation_paused = False
        self.dictation_manually_paused = False
        self.task_contexts.clear()
        self.last_activity_time = time.time()
        self.audio_files.clear()
        
        logger.debug("客户端状态重置完成")
    
    def start_recording(self, start_time: float) -> None:
        """
        开始录音
        
        Args:
            start_time: 录音开始的时间戳
        """
        self.recording = True
        self.recording_start_time = start_time
        logger.debug(f"录音状态已更新: recording=True, start_time={start_time:.2f}")
    
    def stop_recording(self) -> float:
        """
        停止录音
        
        Returns:
            录音持续时间（秒）
        """
        duration = 0.0
        if self.recording_start_time > 0:
            duration = time.time() - self.recording_start_time
        
        self.recording = False
        self.recording_start_time = 0.0
        logger.debug(f"录音状态已更新: recording=False, duration={duration:.2f}s")
        return duration
    
    @property
    def is_connected(self) -> bool:
        """检查 WebSocket 是否已连接"""
        if self.websocket is None:
            return False
        try:
            return not self.websocket.closed
        except AttributeError:
            return self.websocket is not None
    
    def register_audio_file(self, task_id: str, file_path: Path) -> None:
        """
        注册音频文件
        
        Args:
            task_id: 任务ID
            file_path: 音频文件路径
        """
        self.audio_files[task_id] = file_path
        logger.debug(f"注册音频文件: task_id={task_id}, path={file_path}")
    
    def pop_audio_file(self, task_id: str) -> Optional[Path]:
        """
        获取并移除音频文件路径
        
        Args:
            task_id: 任务ID
            
        Returns:
            音频文件路径，如果不存在则返回 None
        """
        file_path = self.audio_files.pop(task_id, None)
        if file_path:
            logger.debug(f"获取音频文件: task_id={task_id}, path={file_path}")
        return file_path

    def set_output_text(self, text: str) -> None:
        """
        设置最近一次输出文本
        
        Args:
            text: 输出文本内容
        """
        self.last_output_text = text
