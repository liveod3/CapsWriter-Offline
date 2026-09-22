# coding: utf-8
"""
Server dataclasses.

Define Task and Result records used by the server.
Use dataclasses for explicit fields and type annotations.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional


# Internal identity; task IDs are unique only within a connection.
TaskKey = tuple[str, str]


@dataclass
class Task:
    """
    Recognition task.

    Carry audio and metadata to the recognition process.

    Attributes:
        type: Task type ('mic', 'file', or 'cmd').
        data: Raw audio bytes (float32, 16 kHz, mono).
        offset: Segment offset within the complete audio, in seconds.
        overlap: Segment overlap in seconds for deduplication.
        task_id: Task identifier.
        socket_id: WebSocket connection identifier.
        is_final: Whether this is the final segment.
        time_start: Recording or audio start timestamp.
        time_submit: Task submission timestamp.
        samplerate: Sample rate in Hz; default 16000.
    """
    type: str
    data: bytes
    offset: float
    overlap: float
    task_id: str
    socket_id: str
    is_final: bool
    time_start: float
    time_submit: float
    context: str = ''
    language: str = 'auto'
    samplerate: int = 16000
    command: str = ''           # Special command, such as 'gpu_boost' or 'gpu_unboost'.
    supports_task_errors: bool = False
    # Internal, pickle-safe snapshot set by the server at task admission.
    formatting: Optional[tuple[bool, bool]] = None

    @property
    def key(self) -> TaskKey:
        """Return the connection-scoped identity used by the worker."""
        return self.socket_id, self.task_id


@dataclass
class Result:
    """
    Recognition result.
    
    Carry results from the recognition process.
    
    Attributes:
        task_id: Task identifier.
        socket_id: WebSocket connection identifier.
        source: Audio source ('mic' or 'file').
        duration: Total processed audio duration in seconds.
        time_start: Recording or audio start timestamp.
        time_submit: Segment submission timestamp.
        time_complete: Recognition completion timestamp.
        
        text: Timestamp-independent merged text for dictation.
        text_accu: Timestamp-deduplicated text for subtitles.
        tokens: Word/character tokens corresponding to timestamps.
        timestamps: Token timestamps in seconds.
        
        is_final: Whether all segments have completed.
    """
    task_id: str
    socket_id: str
    type: str

    duration: float = 0.0
    time_start: float = 0.0
    time_submit: float = 0.0
    time_complete: float = 0.0
    
    # Main output from text merging.
    text: str = ''
    
    # Timestamp-based merged output.
    text_accu: str = ''
    tokens: List[str] = field(default_factory=list)
    timestamps: List[float] = field(default_factory=list)
    
    is_final: bool = False
    error_code: str = ''
    supports_task_errors: bool = False
    close_connection: bool = False

@dataclass
class RecognitionSession:
    """Intermediate recognition state for one (socket_id, task_id) pair."""
    task_id: str
    result: Result
    # Session-level extensions can hold hypotheses or intermediate feature caches.


@dataclass
class AlignmentItem:
    """Neutral alignment record that avoids importing aligner code into ASR."""
    text: str
    start_time: float
    end_time: float


@dataclass
class AlignmentResult:
    """Cross-process alignment result."""
    items: List[AlignmentItem] = field(default_factory=list)


@dataclass
class AlignRequest:
    """Request from ASR to the independent aligner process."""
    request_id: str
    task_id: str
    audio: Any
    text: str
    language: str = 'auto'
    offset_sec: float = 0.0


@dataclass
class AlignResponse:
    """Response from the independent aligner process to ASR."""
    request_id: str
    task_id: str
    result: Optional[AlignmentResult] = None
    error: str = ''
