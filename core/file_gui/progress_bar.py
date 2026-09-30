"""Render confirmed file progress without native animation or interpolation."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QProgressBar


class FileProgressBar(QProgressBar):
    """Keep Qt accessibility while painting confirmed chunks without animation.

    Callers without chunk metadata retain the solid confirmed-duration bar.
    """

    TRACK_COLOR = QColor('#eceaf7')
    FILL_COLOR = QColor('#7969d9')
    ACTIVE_COLOR = QColor('#e1dcfa')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.chunks_completed = None
        self.chunks_total = None
        self.chunk_active = False
        self.segment_seconds = 60.0
        self.audio_seconds = None

    def set_chunks(self, completed, total, active, *, segment_seconds=60.0, audio_seconds=None):
        self.chunks_completed = max(0, completed)
        self.chunks_total = total
        self.chunk_active = active
        self.segment_seconds = segment_seconds
        self.audio_seconds = audio_seconds
        self.update()

    def visible_chunks(self):
        """Keep long files legible with a bounded window around the current chunk."""
        completed = self.chunks_completed or 0
        total = self.chunks_total or (completed + 1)
        capacity = max(1, min(40, self.width() // 16))
        start = max(0, min(completed - capacity // 2, total - capacity))
        return start, min(total, start + capacity)

    def chunk_rects(self):
        """Allocate visible widths by unique audio duration, excluding overlap.

        All non-final chunks advance by the segment stride. Unknown durations
        retain that width until the final confirmed duration becomes available.
        Only the visible window is allocated, even for very large chunk counts.
        """
        start, end = self.visible_chunks()
        durations = [self.segment_seconds] * (end - start)
        if self.audio_seconds is not None and self.chunks_total and end == self.chunks_total:
            tail = self.audio_seconds - (self.chunks_total - 1) * self.segment_seconds
            if tail > 0:
                durations[-1] = tail
        rect = self.contentsRect()
        gap = 4 if len(durations) > 1 else 0
        available = max(0, rect.width() - 2 - gap * (len(durations) - 1))
        scale = available / sum(durations) if durations else 0
        left = rect.x() + 1.0
        blocks = []
        for index, seconds in enumerate(durations, start):
            width = seconds * scale
            blocks.append((index, QRectF(left, rect.y() + 1, width, max(0, rect.height() - 2))))
            left += width + gap
        return blocks

    def paintEvent(self, event):
        rect = self.contentsRect()
        painter = QPainter(self)
        if self.chunks_completed is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            for index, block in self.chunk_rects():
                active = self.chunk_active and index == self.chunks_completed
                painter.setPen(QPen(self.FILL_COLOR, 1.5) if active else Qt.PenStyle.NoPen)
                painter.setBrush(self.FILL_COLOR if index < self.chunks_completed else
                                 self.ACTIVE_COLOR if active else self.TRACK_COLOR)
                radius = min(3, block.width() / 2)
                painter.drawRoundedRect(block, radius, radius)
            painter.end()
            return
        painter.fillRect(rect, self.TRACK_COLOR)
        span = self.maximum() - self.minimum()
        completed = self.value() - self.minimum()
        if span > 0 and completed > 0:
            scale = self.devicePixelRatioF()
            # Align the end to physical pixels, including fractional Windows DPI.
            pixels = round(rect.width() * scale) * min(completed, span) // span
            width = pixels / scale
            painter.fillRect(QRectF(rect.x(), rect.y(), width, rect.height()), self.FILL_COLOR)
        painter.end()
