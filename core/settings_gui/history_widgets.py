"""Expandable recognition rows with independent, anchored pixel scrolling."""

from PySide6.QtCore import QAbstractAnimation, QDate, QEasingCurve, QLocale, QPropertyAnimation, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from core.i18n import get_language, tr
from .history_view import plain_label
from .recent_words import RecordButton


def date_label(value, *, relative=False):
    day = QDate.fromString(value, 'yyyy-MM-dd')
    if not day.isValid():
        return value
    if relative and day == QDate.currentDate():
        return tr('gui.history_period_today')
    locale = QLocale('zh_CN' if get_language() == 'zh-CN' else 'en_US')
    return locale.toString(day, tr('gui.history_date_format'))


class HistoryPagination(QFrame):
    """Keep range and page controls together, wrapping only when they do not fit."""

    def __init__(self):
        super().__init__()
        self.setObjectName('historyPagination')
        self.box = QGridLayout(self)
        self.box.setContentsMargins(12, 8, 12, 8)
        self.box.setHorizontalSpacing(14)
        self.box.setVerticalSpacing(4)
        self.box.setColumnStretch(0, 1)
        self.position = self.navigation = None
        self.stacked = True

    def set_controls(self, position, navigation):
        self.position, self.navigation = position, navigation
        self.box.addWidget(position, 0, 1, Qt.AlignmentFlag.AlignRight)
        self.box.addWidget(navigation, 1, 1, Qt.AlignmentFlag.AlignRight)

    def arrange(self):
        if self.position is None or not self.isVisible():
            return
        stacked = self.position.sizeHint().width() + self.navigation.sizeHint().width() + 52 > self.width()
        if stacked != self.stacked:
            self.stacked = stacked
            self.box.removeWidget(self.navigation)
            self.box.addWidget(self.navigation, 1 if stacked else 0, 1 if stacked else 2,
                               Qt.AlignmentFlag.AlignRight)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange()

    def showEvent(self, event):
        super().showEvent(event)
        self.arrange()


class Chevron(QWidget):
    def __init__(self):
        super().__init__()
        self.expanded = False
        self.setFixedSize(16, 18)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#807591'), 1.4))
        edge, middle = (10, 6) if self.expanded else (6, 10)
        painter.drawLine(4, edge, 8, middle)
        painter.drawLine(8, middle, 12, edge)


class HistoryRow(QFrame):
    toggled = Signal(object)

    def __init__(self, entry):
        super().__init__()
        self.entry = entry
        self.detail = None
        self.loading = False
        self.setObjectName('historyRow')
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(0)
        self.header = RecordButton()
        self.header.setObjectName('historyRowHeader')
        self.header.setCheckable(True)
        caption = date_label(entry['day']) + ' ' + (entry['time'] or tr('gui.history_day'))
        self.header.setAccessibleName(caption + ' ' + entry['preview'])
        self.header.setAccessibleDescription(tr('gui.history_expand'))
        line = QHBoxLayout(self.header)
        line.setContentsMargins(10, 14, 10, 14)
        line.setSpacing(14)
        timestamp = QLabel(entry['time'] or tr('gui.history_day'))
        timestamp.setObjectName('muted')
        timestamp.setMinimumWidth(56)
        preview = self.preview = plain_label()
        preview.setObjectName('historyRowPreview')
        preview.setText(entry['preview'])
        preview.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.arrow = Chevron()
        for widget in (timestamp, preview, self.arrow):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        line.addWidget(timestamp, 0, Qt.AlignmentFlag.AlignTop)
        line.addWidget(preview, 1)
        line.addWidget(self.arrow, 0, Qt.AlignmentFlag.AlignTop)
        self.box.addWidget(self.header)
        self.note = plain_label()
        self.note.setObjectName('muted')
        self.note.setContentsMargins(14, 4, 14, 14)
        self.note.hide()
        self.box.addWidget(self.note)
        self.header.toggled.connect(lambda _: self.toggled.emit(self))

    @property
    def expanded(self):
        return self.header.isChecked()

    def update_expansion(self):
        self.arrow.expanded = self.expanded
        self.arrow.update()
        self.preview.setProperty('expanded', self.expanded)
        self.preview.style().unpolish(self.preview)
        self.preview.style().polish(self.preview)
        self.header.setAccessibleDescription(tr('gui.history_collapse' if self.expanded else 'gui.history_expand'))
        self.note.setVisible(self.expanded and self.detail is None)
        if self.detail is not None:
            self.detail.setVisible(self.expanded)


class HistoryList(QScrollArea):
    """Own only one page of rows; preserve the visible anchor across detail layout."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setObjectName('historyList')
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(180)
        self.content = QWidget()
        self.content.setObjectName('historyListContent')
        self.box = QVBoxLayout(self.content)
        self.box.setContentsMargins(10, 8, 10, 14)
        self.box.setSpacing(0)
        self.box.addStretch()
        self.setWidget(self.content)
        self.rows = []
        self.empty_hint = plain_label()
        self.empty_hint.setObjectName('emptyHint')
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setText(tr('gui.history_ready'))
        self.box.insertWidget(0, self.empty_hint, 1)
        self.scroll_animation = QPropertyAnimation(self.verticalScrollBar(), b'value', self)
        self.scroll_animation.setDuration(150)
        self.scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anchor = None
        self.anchor_timer = QTimer(self)
        self.anchor_timer.setSingleShot(True)
        self.anchor_timer.timeout.connect(self.restore_anchor)
        self.verticalScrollBar().sliderPressed.connect(self.cancel_anchor)
        self.verticalScrollBar().actionTriggered.connect(self.cancel_anchor)
        self.verticalScrollBar().sliderPressed.connect(self.stop_scroll)
        self.verticalScrollBar().actionTriggered.connect(self.stop_scroll)
        self.verticalScrollBar().rangeChanged.connect(self.stop_scroll)

    def keep_anchor(self, row=None):
        if self.anchor is not None:
            return
        self.stop_scroll()
        if row is None:
            row = next((item for item in self.rows if item.mapTo(self.viewport(), QPoint()).y() + item.height() > 0), None)
        if row is not None and row in self.rows:
            self.anchor = (row, row.mapTo(self.viewport(), QPoint()).y())
            self.anchor_passes = 3
            self.anchor_timer.start(0)

    def restore_anchor(self):
        if self.anchor is None:
            return
        row, y = self.anchor
        if row in self.rows:
            self.box.activate()
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + row.mapTo(self.viewport(), QPoint()).y() - y)
        self.anchor_passes -= 1
        if row in self.rows and self.anchor_passes:
            # Wrapped child fields can post another layout after the parent's layout.
            self.anchor_timer.start(0)
        else:
            self.anchor = None

    def cancel_anchor(self, *_):
        self.anchor_timer.stop()
        self.anchor = None

    def replace_rows(self, rows):
        self.cancel_anchor()
        self.stop_scroll()
        self.rows = rows
        while self.box.count():
            item = self.box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget in rows:
                    continue
                widget.hide()
                widget.deleteLater()
        day = None
        for row in rows:
            if row.entry['day'] != day:
                day = row.entry['day']
                label = QLabel(date_label(day, relative=True))
                label.setObjectName('historyDateGroup')
                self.box.addWidget(label)
            self.box.addWidget(row)
        self.empty_hint = plain_label()
        self.empty_hint.setObjectName('emptyHint')
        self.empty_hint.setText(tr('gui.history_empty'))
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setVisible(not rows)
        self.box.addWidget(self.empty_hint, 1 if not rows else 0)
        if rows:
            self.box.addStretch()

    def stop_scroll(self, *_):
        self.scroll_animation.stop()

    def wheelEvent(self, event):
        self.cancel_anchor()
        bar = self.verticalScrollBar()
        if not event.pixelDelta().isNull() or not event.angleDelta().y() or event.modifiers():
            self.stop_scroll()
            super().wheelEvent(event)
            return
        delta = -event.angleDelta().y() / 120 * self.fontMetrics().height() * QApplication.wheelScrollLines()
        target = bar.value()
        if self.scroll_animation.state() == QAbstractAnimation.State.Running:
            previous_target = self.scroll_animation.endValue()
            if (previous_target - target) * delta > 0:
                target = previous_target
        target = max(bar.minimum(), min(bar.maximum(), round(target + delta)))
        self.stop_scroll()
        if target == bar.value():
            event.ignore()
            return
        self.scroll_animation.setStartValue(bar.value())
        self.scroll_animation.setEndValue(target)
        self.scroll_animation.start()
        event.accept()

    def keyPressEvent(self, event):
        self.cancel_anchor()
        self.stop_scroll()
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self.cancel_anchor()
        self.stop_scroll()
        super().hideEvent(event)
