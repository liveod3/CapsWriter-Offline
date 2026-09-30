"""Retain native queue interaction while smoothing each mouse-wheel row step."""

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QEvent, QItemSelectionModel, QPoint, QRect, QRectF,
    QTimer, QVariantAnimation, Qt, Signal,
)
from PySide6.QtGui import QColor, QDrag, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QListWidget, QMenu


class QueueMenu(QMenu):
    """Keep native action interaction while emphasizing queue removal in red."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for action in self.actions():
            if not action.property('destructive') or not action.isVisible():
                continue
            rectangle = self.actionGeometry(action)
            hovered = self.activeAction() is action and action.isEnabled()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#fff0f1' if hovered else '#ffffff'))
            painter.drawRoundedRect(rectangle, 5, 5)
            painter.setFont(action.font())
            painter.setPen(QColor('#a83245' if action.isEnabled() else '#9ca2b2'))
            content = rectangle.adjusted(24, 0, -24, 0)
            right_to_left = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
            label_align = Qt.AlignmentFlag.AlignRight if right_to_left else Qt.AlignmentFlag.AlignLeft
            shortcut_align = Qt.AlignmentFlag.AlignLeft if right_to_left else Qt.AlignmentFlag.AlignRight
            painter.drawText(content, Qt.AlignmentFlag.AlignVCenter | label_align, action.text())
            painter.drawText(content, Qt.AlignmentFlag.AlignVCenter | shortcut_align,
                             action.shortcut().toString(QKeySequence.SequenceFormat.NativeText))
        painter.end()


class QueueCheckBox(QCheckBox):
    """A quiet indicator with the full checkbox hit target and accessibility."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF((self.width() - 14) / 2, (self.height() - 14) / 2, 14, 14)
        color = QColor('#887bc4' if self.isChecked() else '#c9cbd6')
        painter.setPen(QPen(color, 1))
        painter.setBrush(QColor('#f1eefb') if self.isChecked() else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 3, 3)
        if self.isChecked():
            tick = QPainterPath()
            tick.moveTo(box.left() + 3, box.top() + 7)
            tick.lineTo(box.left() + 6, box.top() + 10)
            tick.lineTo(box.left() + 11, box.top() + 4)
            painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(tick)
        painter.end()


class FileQueueList(QListWidget):
    """Animate wheel notches in pixels without changing touchpad or keyboard steps."""

    move_requested = Signal(list, int)
    remove_requested = Signal()
    step_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editable = True
        self.check_mode = False
        self._sweep_row = None
        self._sweep_original = ()
        self._sweep_value = False
        self._sweep_position = QPoint()
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(30)
        self._sweep_timer.timeout.connect(self._sweep_scroll)
        self.itemSelectionChanged.connect(self.sync_checks)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._wheel_target = 0.0
        self._wheel_direction = 0
        self._applying_wheel = False
        self._wheel_animation = QVariantAnimation(self)
        self._wheel_animation.setDuration(150)
        self._wheel_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._wheel_animation.valueChanged.connect(self._apply_wheel_value)
        scrollbar = self.verticalScrollBar()
        scrollbar.valueChanged.connect(self._scroll_value_changed)
        scrollbar.rangeChanged.connect(self._stop_wheel)
        scrollbar.sliderPressed.connect(self._stop_wheel)
        scrollbar.actionTriggered.connect(self._stop_wheel)
        scrollbar.installEventFilter(self)

    def set_check_mode(self, enabled):
        self._stop_sweep()
        self.check_mode = enabled
        self.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection if enabled
                              else QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(self.editable and not enabled)
        self.sync_checks()

    def sync_checks(self):
        for index in range(self.count()):
            item = self.item(index)
            row = self.itemWidget(item)
            check = row.findChild(QCheckBox, 'queueCheck') if row else None
            if check is not None:
                check.setVisible(self.check_mode)
                blocked = check.blockSignals(True)
                check.setChecked(item.isSelected())
                check.blockSignals(blocked)

    def check_rect(self, item):
        row = self.visualItemRect(item)
        return QRect(row.left() + 4, row.center().y() - 12, 24, 24)

    def _stop_sweep(self):
        self._sweep_timer.stop()
        self._sweep_row = None
        self._sweep_original = ()

    def _paint_selection(self, row):
        if self._sweep_row is None:
            return
        changed = False
        lower, upper = sorted((row, self._sweep_row))
        blocked = self.blockSignals(True)
        try:
            for index, original in enumerate(self._sweep_original):
                value = self._sweep_value if lower <= index <= upper else original
                item = self.item(index)
                if item.isSelected() != value:
                    item.setSelected(value)
                    changed = True
        finally:
            self.blockSignals(blocked)
        if changed and not blocked:
            self.itemSelectionChanged.emit()

    def _sweep_scroll(self):
        if self._sweep_row is None:
            return
        y = self._sweep_position.y()
        height = self.viewport().height()
        delta = -12 if y < 24 else 12 if y >= height - 24 else 0
        if delta:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + delta)
            self._sweep_at(self._sweep_position)

    def _sweep_at(self, position):
        if not self.count():
            return
        y = max(0, min(self.viewport().height() - 1, position.y()))
        item = self.itemAt(QPoint(16, y))
        if item is None:
            # Coalesced movement can land in row spacing or below the last row.
            item = min((self.item(i) for i in range(self.count())),
                       key=lambda candidate: abs(self.visualItemRect(candidate).center().y() - y))
        self._paint_selection(self.row(item))

    def clear(self):
        self._stop_sweep()
        super().clear()

    def _row_step(self):
        """Measure the visible row pitch, including QListView item spacing."""
        index = self.indexAt(QPoint(self.viewport().width() // 2, 1))
        row = index.row() if index.isValid() else max(0, self.currentRow())
        if row < self.count():
            rectangle = self.visualItemRect(self.item(row))
            if row + 1 < self.count():
                distance = self.visualItemRect(self.item(row + 1)).top() - rectangle.top()
                if distance > 0:
                    return distance
            if rectangle.height() > 0:
                return rectangle.height() + self.spacing() * 2
        return max(1, self.fontMetrics().height())

    def _stop_wheel(self, *_):
        animation = getattr(self, '_wheel_animation', None)
        if animation is not None:
            animation.stop()
            self._wheel_target = float(self.verticalScrollBar().value())
            self._wheel_direction = 0

    def _scroll_value_changed(self, _value):
        if not self._applying_wheel:
            self._stop_wheel()

    def _apply_wheel_value(self, value):
        # Setting new animation endpoints can emit valueChanged before start.
        if self._wheel_animation.state() != QAbstractAnimation.State.Running:
            return
        self._applying_wheel = True
        try:
            self.verticalScrollBar().setValue(round(value))
        finally:
            self._applying_wheel = False

    def _scroll_wheel(self, event):
        scrollbar = self.verticalScrollBar()
        pixels = event.pixelDelta()
        if not pixels.isNull():
            self._stop_wheel()
            if pixels.y() == 0:
                return False
            scrollbar.setValue(scrollbar.value() - pixels.y())
            event.accept()
            return True
        angle = event.angleDelta().y()
        if angle == 0 or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._stop_wheel()
            return False
        delta = -angle / 120 * self._row_step()
        direction = 1 if delta > 0 else -1
        # Same-direction notches accumulate their destination; reversing starts
        # at the visible position so queued travel never delays the reversal.
        base = self._wheel_target if direction == self._wheel_direction else scrollbar.value()
        target = max(scrollbar.minimum(), min(scrollbar.maximum(), base + delta))
        self._wheel_animation.stop()
        self._wheel_target = float(target)
        self._wheel_direction = direction
        if round(target) != scrollbar.value():
            self._wheel_animation.setStartValue(float(scrollbar.value()))
            self._wheel_animation.setEndValue(float(target))
            self._wheel_animation.start()
        event.accept()
        return True

    def wheelEvent(self, event):
        if not self._scroll_wheel(event):
            super().wheelEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.verticalScrollBar():
            if event.type() == QEvent.Type.Wheel and self._scroll_wheel(event):
                return True
            if event.type() == QEvent.Type.MouseButtonPress:
                self._stop_wheel()
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        self._stop_wheel()
        self._stop_sweep()
        if self.check_mode and event.key() == Qt.Key.Key_Space and self.currentItem() is not None:
            item = self.currentItem()
            item.setSelected(not item.isSelected())
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.remove_requested.emit()
            event.accept()
            return
        if event.modifiers() == Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.step_requested.emit(-1 if event.key() == Qt.Key.Key_Up else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions):
        if self.check_mode or not self.editable or not self.selectedItems():
            return
        self._stop_wheel()
        drag = QDrag(self)
        drag.setMimeData(self.mimeData(self.selectedItems()))
        preview = self.visualItemRect(self.selectedItems()[0]).intersected(self.viewport().rect())
        if not preview.isEmpty():
            drag.setPixmap(self.viewport().grab(preview))
            drag.setHotSpot(QPoint(min(24, preview.width() // 2), preview.height() // 2))
        # The owner moves entries atomically; Qt must not delete source rows.
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            drag.deleteLater()

    def dragEnterEvent(self, event):
        if event.source() is self:
            if self.editable and not self.check_mode:
                super().dragEnterEvent(event)
            else:
                event.ignore()
        elif self.window() is not self:
            self.window().dragEnterEvent(event)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.source() is self:
            if self.editable and not self.check_mode:
                super().dragMoveEvent(event)
            else:
                event.ignore()
        elif self.window() is not self:
            self.window().dragMoveEvent(event)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        super().dragLeaveEvent(event)
        if self.window() is not self:
            self.window().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.source() is not self:
            if self.window() is not self:
                self.window().dropEvent(event)
            else:
                event.ignore()
            return
        if not self.editable or self.check_mode:
            event.ignore()
            return
        position = event.position().toPoint()
        item = self.itemAt(position)
        destination = self.count() if item is None else self.row(item) + (
            position.y() >= self.visualItemRect(item).center().y())
        rows = sorted(self.row(item) for item in self.selectedItems())
        self.move_requested.emit(rows, destination)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def mousePressEvent(self, event):
        self._stop_wheel()
        self._stop_sweep()
        if self.check_mode and event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
                value = not item.isSelected()
                self._sweep_row = self.row(item)
                self._sweep_original = tuple(self.item(i).isSelected() for i in range(self.count()))
                self._sweep_value = value
                self._sweep_position = event.position().toPoint()
                self._paint_selection(self._sweep_row)
                self._sweep_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.check_mode and event.buttons() & Qt.MouseButton.LeftButton:
            if self._sweep_row is not None:
                self._sweep_position = event.position().toPoint()
                self._sweep_at(self._sweep_position)
            event.accept()
            return
        self._stop_sweep()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.check_mode and event.button() == Qt.MouseButton.LeftButton:
            self._stop_sweep()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        self._stop_sweep()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._stop_wheel()
        self._stop_sweep()
        super().hideEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate:
            self._stop_wheel()
            self._stop_sweep()
        return super().event(event)
