# coding: utf-8
"""
Server state management.

Define ServerState for the parent and WorkerState for recognition.
"""

from __future__ import annotations

from core.i18n import Notice
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

# Shared server Rich console.
console = Console(highlight=False)


def _bounded_queue(config_name: str, default: int) -> Queue:
    """Create bounded multiprocessing queues with safe defaults."""
    try:
        maxsize = int(getattr(Config, config_name, default))
    except (TypeError, ValueError):
        maxsize = default
    return Queue(maxsize=max(1, maxsize))


@dataclass
class ServerState:
    """
    Parent process state.
    
    Share server runtime state:
    - sockets: WebSocket connections keyed by socket_id.
    - sockets_id: Cross-process socket IDs created through Manager.
    - queue_in: Parent-to-recognition task queue.
    - queue_out: Recognition-to-parent result queue.
    - recognize_process: Recognition subprocess handle.
    """
    app: Optional[CapsWriterServer] = None

    # WebSocket connection pool.
    sockets: Dict[str, websockets.WebSocketServerProtocol] = field(default_factory=dict)
    socket_last_activity: Dict[str, float] = field(default_factory=dict)
    failed_tasks: FailedTasks = field(default_factory=FailedTasks)
    audio_caches: dict[str, dict] = field(default_factory=dict)
    
    # Cross-process socket IDs, initialized with Manager().list().
    sockets_id: Optional[ListProxy] = None
    
    # Message queues.
    queue_in: Queue = field(default_factory=lambda: _bounded_queue('queue_in_maxsize', 32))
    queue_out: Queue = field(default_factory=lambda: _bounded_queue('queue_out_maxsize', 32))
    align_queue_in: Queue = field(default_factory=lambda: _bounded_queue('align_queue_in_maxsize', 4))
    align_queue_out: Queue = field(default_factory=lambda: _bounded_queue('align_queue_out_maxsize', 4))

    # Recognition subprocess.
    recognize_process: Optional[Process] = None
    aligner_process: Optional[Process] = None
    worker_failed: Any = None  # Shared Event, created with the worker's process context.
    worker_progress: Any = None  # Updated by the task loop, never by a heartbeat thread.



@dataclass
class WorkerState:
    """Worker state with sessions keyed by (socket_id, task_id)."""
    sessions: Dict[TaskKey, RecognitionSession] = field(default_factory=dict)
    failed_tasks: FailedTasks = field(default_factory=FailedTasks)
    
    # GPU boost state.
    gpu_boosted: bool = False       # Whether GPU boost has been applied.
    gpu_last_active: float = 0.0    # Last task activity for idle boost reset.

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
            logger.debug(Notice('diagnostic.state.removed_disconnected_sessions', value0=len(stale_keys)))
        return len(stale_keys)
