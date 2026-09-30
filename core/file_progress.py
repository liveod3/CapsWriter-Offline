"""Share file-transcription timing and display metrics without client imports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping
import time


def format_duration(seconds: float) -> str:
    """Format seconds with the terminal's nearest-second clock convention."""
    seconds = max(0.0, seconds)
    hours, remainder = divmod(int(seconds + 0.5), 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    if hours:
        return f'{hours:d}:{minutes:02d}:{whole_seconds:02d}'
    return f'{minutes:02d}:{whole_seconds:02d}'


@dataclass(frozen=True)
class ProgressSnapshot:
    """One confirmed audio position with independently advancing wall-clock metrics.

    Elapsed time starts when transcription starts, after preparation and probing.
    Advancing a snapshot counts down the measured ETA. The last measured
    throughput remains unchanged until recognition confirms more audio; it never
    estimates additional recognized audio.
    """

    processed_seconds: float = 0.0
    total_seconds: float | None = None
    elapsed_seconds: float = 0.0
    eta_seconds: float | None = None
    observed_speed: float | None = None
    chunks_completed: int = 0
    chunk_seconds: float = 0.0
    chunk_elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        # Rich progress expands an underestimated known duration to the latest
        # confirmed position. Keep GUI durations and percentages identical.
        if self.total_seconds is not None:
            total = (max(self.total_seconds, self.processed_seconds)
                     if self.total_seconds > 0 else None)
            object.__setattr__(self, 'total_seconds', total)

    @classmethod
    def from_event(cls, event: Mapping[str, Any]) -> ProgressSnapshot:
        """Read timing fields from an already-validated local worker event."""
        return cls(
            processed_seconds=event.get('processed_seconds', 0.0),
            total_seconds=event.get('total_seconds'),
            elapsed_seconds=event.get('elapsed_seconds', 0.0),
            eta_seconds=event.get('eta_seconds'),
            observed_speed=event.get('speed'),
            chunks_completed=event.get('chunks_completed', 0),
            chunk_seconds=event.get('chunk_seconds', 0.0),
            chunk_elapsed_seconds=event.get('chunk_elapsed_seconds', 0.0),
        )

    def advance(self, seconds: float) -> ProgressSnapshot:
        """Refresh wall time since this snapshot without advancing recognition."""
        seconds = max(0.0, seconds)
        return replace(
            self,
            elapsed_seconds=self.elapsed_seconds + seconds,
            eta_seconds=(max(0.0, self.eta_seconds - seconds)
                         if self.eta_seconds is not None else None),
        )

    @property
    def speed(self) -> float:
        """Return the last measured speed, with a cumulative legacy fallback."""
        if self.observed_speed is not None:
            return self.observed_speed
        return self.processed_seconds / max(self.elapsed_seconds, 1e-6)

    @property
    def rtf(self) -> float | None:
        return (self.elapsed_seconds / self.processed_seconds
                if self.processed_seconds > 0 else None)

    @property
    def percentage(self) -> float | None:
        if self.total_seconds is None:
            return None
        return min(100.0, self.processed_seconds / self.total_seconds * 100)

    @property
    def remaining_seconds(self) -> float | None:
        """Return unrecognized audio duration, separately from wall-clock ETA."""
        if self.total_seconds is None:
            return None
        return max(0.0, self.total_seconds - self.processed_seconds)

    def as_metrics(self) -> dict[str, float | None]:
        """Return the existing content-free metrics used by worker events."""
        return {
            'processed_seconds': self.processed_seconds,
            'total_seconds': self.total_seconds,
            'elapsed_seconds': self.elapsed_seconds,
            'speed': self.speed,
            'rtf': self.rtf,
            'eta_seconds': self.eta_seconds,
            'chunks_completed': self.chunks_completed,
            'chunk_seconds': self.chunk_seconds,
            'chunk_elapsed_seconds': self.chunk_elapsed_seconds,
        }


@dataclass
class ProgressEstimator:
    """Measure individual chunks and their time-weighted cumulative throughput."""

    started_at: float
    completed: float = 0.0
    last_completed: float = 0.0
    last_updated_at: float | None = None
    measured_speed: float = 0.0
    eta_deadline: float | None = None
    chunks_completed: int = 0
    chunk_seconds: float = 0.0
    chunk_elapsed_seconds: float = 0.0

    def update(
        self,
        completed: float,
        total: float | None,
        *,
        now: float | None = None,
    ) -> None:
        now = time.perf_counter() if now is None else now
        completed = max(self.completed, completed)
        # Wall time and repeated results do not provide a new speed measurement.
        # Preserve the existing ETA deadline as well, rather than restarting its
        # countdown whenever an unchanged position is reported.
        if completed <= self.completed:
            if total is not None and self.measured_speed > 0 and self.eta_deadline is None:
                self.eta_deadline = now + max(0.0, total - completed) / self.measured_speed
            return
        elapsed = max(now - self.started_at, 1e-6)
        overall_speed = completed / elapsed

        previous_at = self.last_updated_at if self.last_updated_at is not None else self.started_at
        delta_time = max(now - previous_at, 1e-6)
        delta_audio = max(0.0, completed - self.last_completed)
        # Sum(audio) / sum(wall time), never an average of speed ratios.
        # The first interval includes initial upload/queueing; later intervals
        # run between confirmed results. These are end-to-end, not GPU timings.
        self.measured_speed = overall_speed
        self.chunks_completed += 1
        self.chunk_seconds = delta_audio
        self.chunk_elapsed_seconds = delta_time

        self.completed = completed
        self.last_completed = completed
        self.last_updated_at = now
        if total is not None and self.measured_speed > 0:
            remaining = max(0.0, total - completed)
            self.eta_deadline = now + remaining / self.measured_speed
        else:
            self.eta_deadline = None

    def live_speed(self, *, now: float | None = None) -> float:
        """Return the completed-chunk average, held until another chunk completes."""
        return self.snapshot(now=now).speed

    def eta_seconds(self, *, now: float | None = None) -> float | None:
        """Return measured ETA, counting down by wall time between progress updates."""
        if self.eta_deadline is None:
            return None
        now = time.perf_counter() if now is None else now
        return max(0.0, self.eta_deadline - now)

    def snapshot(
        self, total: float | None = None, *, now: float | None = None,
    ) -> ProgressSnapshot:
        """Sample the same confirmed duration, elapsed time, speed and ETA for any UI."""
        now = time.perf_counter() if now is None else now
        return ProgressSnapshot(
            processed_seconds=self.completed,
            total_seconds=total,
            elapsed_seconds=max(0.0, now - self.started_at),
            eta_seconds=self.eta_seconds(now=now),
            observed_speed=self.measured_speed,
            chunks_completed=self.chunks_completed,
            chunk_seconds=self.chunk_seconds,
            chunk_elapsed_seconds=self.chunk_elapsed_seconds,
        )
