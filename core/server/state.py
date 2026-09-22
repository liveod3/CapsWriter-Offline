# coding: utf-8
"""
服务端状态管理模块

提供 ServerState (主进程) 和 WorkerState (子进程) 类。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from multiprocessing import Queue, Process
from multiprocessing.managers import ListProxy
from typing import TYPE_CHECKING, Any, Dict, Optional

import websockets
from rich.console import Console
from config_server import ServerConfig as Config

from core.server.schema import Result, RecognitionSession, TaskKey
from core.server.task_failures import FailedTasks

if TYPE_CHECKING:
    from .app import CapsWriterServer

# Rich console 用于控制台输出（服务端统一使用此实例）
console = Console(highlight=False)


def _bounded_queue(config_name: str, default: int) -> Queue:
    """使用安全默认值创建有界多进程队列。"""
    try:
        maxsize = int(getattr(Config, config_name, default))
    except (TypeError, ValueError):
        maxsize = default
    return Queue(maxsize=max(1, maxsize))


@dataclass
class ServerState:
    """
    主进程运行状态
    
    存储服务端主进程运行时的共享状态：
    - sockets: WebSocket 连接字典，以 socket_id 为键
    - sockets_id: 跨进程的 socket ID 列表（由 Manager 创建）
    - queue_in: 任务输入队列（主进程 -> 识别进程）
    - queue_out: 结果输出队列（识别进程 -> 主进程）
    - recognize_process: 识别子进程句柄
    """
    app: Optional[CapsWriterServer] = None

    # WebSocket 连接池
    sockets: Dict[str, websockets.WebSocketServerProtocol] = field(default_factory=dict)
    socket_last_activity: Dict[str, float] = field(default_factory=dict)
    failed_tasks: FailedTasks = field(default_factory=FailedTasks)
    audio_caches: dict[str, dict] = field(default_factory=dict)
    
    # 跨进程共享的 socket ID 列表（需要用 Manager().list() 初始化）
    sockets_id: Optional[ListProxy] = None
    
    # 消息队列
    queue_in: Queue = field(default_factory=lambda: _bounded_queue('queue_in_maxsize', 32))
    queue_out: Queue = field(default_factory=lambda: _bounded_queue('queue_out_maxsize', 32))
    align_queue_in: Queue = field(default_factory=lambda: _bounded_queue('align_queue_in_maxsize', 4))
    align_queue_out: Queue = field(default_factory=lambda: _bounded_queue('align_queue_out_maxsize', 4))

    # 识别子进程
    recognize_process: Optional[Process] = None
    aligner_process: Optional[Process] = None
    worker_failed: Any = None  # Shared Event, created with the worker's process context.
    worker_progress: Any = None  # Updated by the task loop, never by a heartbeat thread.



@dataclass
class WorkerState:
    """Worker state with sessions keyed by (socket_id, task_id)."""
    sessions: Dict[TaskKey, RecognitionSession] = field(default_factory=dict)
    failed_tasks: FailedTasks = field(default_factory=FailedTasks)
    
    # GPU 加速状态
    gpu_boosted: bool = False       # 当前是否已执行 GPU 加速
    gpu_last_active: float = 0.0    # 上次任务活跃时间，用于超时取消加速

    def get_session(self, task_id: str, socket_id: str, source: str = '') -> RecognitionSession:
        """Get or create a session owned by the specified connection."""
        key = (socket_id, task_id)
        if key not in self.sessions:
            result = Result(task_id=task_id, socket_id=socket_id, type=source)
            self.sessions[key] = RecognitionSession(task_id=task_id, result=result)
        return self.sessions[key]
    
    def cleanup_sessions(self, sockets_id: ListProxy) -> int:
        """Remove sessions whose owning connections have disconnected."""
        active_sockets = set(sockets_id)
        stale_keys = [
            key for key in self.sessions
            if key[0] not in active_sockets
        ]
        for key in stale_keys:
            self.sessions.pop(key, None)
        if stale_keys:
            from . import logger
            logger.debug(f"Removed {len(stale_keys)} disconnected sessions")
        return len(stale_keys)
