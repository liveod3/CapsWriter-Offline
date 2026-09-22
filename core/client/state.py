# coding: utf-8
"""
Client state management.

Store shared client state in ClientState.
Use dataclasses for explicit fields and type annotations.
"""

from __future__ import annotations

from core.i18n import Notice

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


# Configure the Rich console.
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
    # Retain legacy color tags so the client uses one base palette.
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
    Client runtime state.

    Share the event loop, message queues,
    WebSocket connection, audio stream, and recording state.

    Attributes:
        loop: asyncio event loop.
        queue_in: Incoming audio queue.
        queue_out: Reserved result output queue.
        websocket: Client WebSocket connection.
        stream: Audio input stream.
        recording: Whether recording is active.
        recording_start_time: Recording start timestamp.
        audio_files: Mapping from task IDs to audio paths.
        last_recognition_text: Most recent final ASR text, before LLM processing.
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
    # Event-loop-owned upload completion and final-result deadlines.
    dictation_uploads: dict[str, asyncio.Event] = field(default_factory=dict)
    dictation_deadlines: dict[str, float] = field(default_factory=dict)
    last_activity_time: float = field(default_factory=time.time)
    audio_files: Dict[str, Path] = field(default_factory=dict)

    # Most recent recognition result before LLM processing.
    last_recognition_text: Optional[str] = None
    
    # Most recent output: LLM output when used, otherwise the ASR result.
    last_output_text: Optional[str] = None
    

    
    def reset(self) -> None:
        """
        Reset shared state.
        
        Clear state and release connections and streams for reinitialization or shutdown.
        """
        logger.debug(Notice('diagnostic.state.resetting_client_state'))
        
        # Close the WebSocket connection.
        ws = self.websocket
        if ws is not None:
            try:
                if not ws.closed and self.app and self.app.loop and self.app.loop.is_running():
                    asyncio.run_coroutine_threadsafe(ws.close(), self.app.loop)
            except Exception:
                pass
            self.websocket = None
        
        # Close the audio stream.
        if self.stream is not None:
            try:
                self.stream.close()
                logger.debug(Notice('diagnostic.state.audio_stream_closed'))
            except Exception:
                pass
            self.stream = None
        
        # Reset remaining state.
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
        self.dictation_uploads.clear()
        self.dictation_deadlines.clear()
        self.last_activity_time = time.time()
        self.audio_files.clear()
        
        logger.debug(Notice('diagnostic.state.client_state_reset_complete'))
    
    def start_recording(self, start_time: float) -> None:
        """
        Start recording.
        
        Args:
            start_time: Recording start timestamp.
        """
        self.recording = True
        self.recording_start_time = start_time
        logger.debug(Notice('diagnostic.state.recording_state_updated_recording_true_start_time', value0=start_time))
    
    def stop_recording(self) -> float:
        """
        Stop recording.
        
        Returns:
            Recording duration in seconds.
        """
        duration = 0.0
        if self.recording_start_time > 0:
            duration = time.time() - self.recording_start_time
        
        self.recording = False
        self.recording_start_time = 0.0
        logger.debug(Notice('diagnostic.state.recording_state_updated_recording_false_duration_s', value0=duration))
        return duration
    
    @property
    def is_connected(self) -> bool:
        """Return whether the WebSocket is connected."""
        if self.websocket is None:
            return False
        try:
            return not self.websocket.closed
        except AttributeError:
            return self.websocket is not None
    
    def register_audio_file(self, task_id: str, file_path: Path) -> None:
        """
        Register an audio file.
        
        Args:
            task_id: Task identifier.
            file_path: Audio file path.
        """
        self.audio_files[task_id] = file_path
        logger.debug(Notice('diagnostic.state.audio_file_registered_task'), task_id[:8])
    
    def pop_audio_file(self, task_id: str) -> Optional[Path]:
        """
        Get and remove an audio path.
        
        Args:
            task_id: Task identifier.
            
        Returns:
            Audio file path, or None if absent.
        """
        file_path = self.audio_files.pop(task_id, None)
        if file_path:
            logger.debug(Notice('diagnostic.state.audio_file_retrieved_task'), task_id[:8])
        return file_path

    def set_output_text(self, text: str) -> None:
        """
        Set the most recent output text.
        
        Args:
            text: Output text.
        """
        self.last_output_text = text
