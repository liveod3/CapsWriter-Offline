"""Bounded, in-memory event text with explicit scroll-follow ownership."""

from datetime import datetime

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPen, QPolygonF, QTextCharFormat, QTextCursor, QTextOption,
)
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from core.i18n import tr


MAX_BLOCKS = 1000
MAX_LINE_CHARS = 2000
COLLAPSED_HEIGHT = 52
LEVEL_COLORS = {
    'info': '#596078',
    'progress': '#6658cc',
    'success': '#26805b',
    'warning': '#96631d',
    'error': '#b24e5b',
    'output': '#29668c',
}


class CollapseButton(QToolButton):
    """Draw a small disclosure chevron independent of native filled-arrow styles."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(28, 28)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.isDown() or self.underMouse():
            painter.setBrush(QColor('#e9e6fc' if self.isDown() else '#f1effb'))
            painter.drawRoundedRect(bounds, 6, 6)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor('#887cda'), 1.2))
            painter.drawRoundedRect(bounds, 6, 6)
        color = '#6658cc' if self.underMouse() or self.hasFocus() else '#70778b'
        if not self.isEnabled():
            color = '#b8bfd0'
        pen = QPen(QColor(color), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x, y = self.width() / 2, self.height() / 2
        points = ([(x - 2, y - 4), (x + 2, y), (x - 2, y + 4)] if self.isChecked()
                  else [(x - 4, y - 2), (x, y + 2), (x + 4, y - 2)])
        painter.drawPolyline(QPolygonF([QPointF(px, py) for px, py in points]))


def _event_lines(message, prefix):
    """Keep multiline events readable without interpreting controls or markup."""
    if not message.strip():
        return []
    message = message[-MAX_BLOCKS * MAX_LINE_CHARS:].replace('\r\n', '\n').replace('\r', '\n')
    lines = []
    for line in message.rstrip('\n').split('\n')[-MAX_BLOCKS:]:
        text = ''.join(
            char if char.isprintable() else '    ' if char == '\t' else f'\\u{ord(char):04x}'
            for char in line[:MAX_LINE_CHARS]
        )
        available = MAX_LINE_CHARS - len(prefix)
        if len(text) > available:
            text = text[:available - 1] + '…'
        lines.append(prefix + text)
    return lines


class FileEventLog(QFrame):
    """Show caller-curated events; never read clipboard data or persist content.

    All methods belong to the Qt thread. Manual upward scrolling disables follow
    until the checkbox is explicitly enabled again. Clearing retains that choice.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.setMinimumHeight(150)
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        self.title = QLabel(tr('files.log_title'))
        self.title.setObjectName('cardTitle')
        heading.addWidget(self.title)
        heading.addStretch()
        self.collapse_button = CollapseButton()
        self.collapse_button.setToolTip(tr('files.log_collapse'))
        self.collapse_button.setAccessibleName(tr('files.log_collapse'))
        self.collapse_button.setAccessibleDescription(tr('files.log_title'))
        heading.addWidget(self.collapse_button)
        layout.addLayout(heading)
        self.controls = QWidget()
        self.controls.setFixedHeight(28)
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.follow = QCheckBox(tr('files.log_follow'))
        self.follow.setChecked(True)
        controls.addWidget(self.follow)
        controls.addStretch()
        self.clear_button = QPushButton(tr('files.log_clear'))
        self.clear_button.setObjectName('subtle')
        self.clear_button.setFixedHeight(28)
        self.clear_button.setStyleSheet(
            'QPushButton { padding: 3px 8px; min-height: 16px; }'
            'QPushButton:focus { padding: 2px 7px; }'
        )
        self.clear_button.setEnabled(False)
        self.clear_button.clicked.connect(self.confirm_clear)
        controls.addWidget(self.clear_button)
        self.editor = QTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setAcceptRichText(False)
        self.editor.setUndoRedoEnabled(False)
        self.editor.document().setMaximumBlockCount(MAX_BLOCKS)
        self.editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.editor.setMinimumHeight(70)
        self.editor.setAccessibleName(tr('files.log_title'))
        self.editor.setPlaceholderText(tr('files.log_empty'))
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        installed = set(QFontDatabase.families())
        for family in ('Consolas', 'Lucida Console'):
            if family in installed:
                font.setFamily(family)
                break
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setPixelSize(13)
        self.editor.setFont(font)
        self.editor.document().setDefaultFont(font)
        self.editor.setStyleSheet(
            'QTextEdit { background: #fafbfe; border: 1px solid #dfe3ee; '
            'border-radius: 6px; padding: 7px 10px; '
            'font-size: 13px; '
            'selection-background-color: #dcd7fa; selection-color: #272043; }'
            'QTextEdit:focus { border-color: #887cda; }'
        )
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.controls)
        scrollbar = self.editor.verticalScrollBar()
        scrollbar.valueChanged.connect(self._scroll_changed)
        scrollbar.rangeChanged.connect(self._range_changed)
        self.follow.toggled.connect(self._follow_changed)
        self.collapse_button.toggled.connect(self._set_collapsed)
        self.editor.document().contentsChanged.connect(
            lambda: self.clear_button.setEnabled(not self.editor.document().isEmpty()))

    def append(self, message: str, level: str = 'info'):
        """Insert styled plain text without interpreting caller text as HTML."""
        prefix = datetime.now().strftime('[%H:%M:%S] ')
        lines = _event_lines(message, prefix)
        if not lines:
            return
        document = self.editor.document()
        old_blocks = 0 if document.isEmpty() else document.blockCount()
        scrollbar = self.editor.verticalScrollBar()
        anchor = self.editor.cursorForPosition(QPoint(0, 0))
        anchor.setPosition(anchor.block().position())
        old_block_number = anchor.blockNumber()
        anchor_top = self.editor.cursorRect(anchor).top()
        horizontal = self.editor.horizontalScrollBar()
        previous_horizontal = horizontal.value()
        trimmed = max(0, old_blocks + len(lines) - document.maximumBlockCount())
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(LEVEL_COLORS.get(level, LEVEL_COLORS['info'])))
        if level in {'success', 'warning', 'error'}:
            text_format.setFontWeight(QFont.Weight.DemiBold)
        timestamp_format = QTextCharFormat()
        timestamp_format.setForeground(QColor('#9299ab'))
        self._updating = True
        try:
            cursor = QTextCursor(document)
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.beginEditBlock()
            for line in lines:
                if not document.isEmpty():
                    cursor.insertBlock()
                cursor.insertText(line[:len(prefix)], timestamp_format)
                cursor.insertText(line[len(prefix):], text_format)
            cursor.endEditBlock()
            if self.follow.isChecked():
                scrollbar.setValue(scrollbar.maximum())
            elif old_block_number < trimmed:
                scrollbar.setValue(0)
            else:
                # QTextEdit scrolls in pixels. Keep a live cursor anchored to the
                # same retained block, including its partial-line offset.
                displacement = self.editor.cursorRect(anchor).top() - anchor_top
                scrollbar.setValue(scrollbar.value() + displacement)
            # Following owns vertical position only; native append behavior may
            # otherwise hide timestamps by scrolling a long line to its end.
            horizontal.setValue(previous_horizontal)
        finally:
            self._updating = False

    def clear(self):
        """Clear programmatically; only the visible button requires confirmation."""
        self._updating = True
        try:
            self.editor.clear()
        finally:
            self._updating = False

    def confirm_clear(self):
        if self.editor.document().isEmpty():
            return
        confirmation = QMessageBox(
            QMessageBox.Icon.Question,
            tr('files.log_clear_confirm_title'), tr('files.log_clear_confirm_body'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self,
        )
        confirmation.button(QMessageBox.StandardButton.Yes).setText(tr('files.log_clear'))
        confirmation.button(QMessageBox.StandardButton.No).setText(tr('files.cancel_settings'))
        confirmation.setDefaultButton(QMessageBox.StandardButton.No)
        confirmation.setEscapeButton(QMessageBox.StandardButton.No)
        answer = confirmation.exec()
        confirmation.deleteLater()
        if answer == QMessageBox.StandardButton.Yes:
            self.clear()

    def _set_collapsed(self, collapsed):
        self._updating = True
        try:
            self.editor.setVisible(not collapsed)
            self.controls.setVisible(not collapsed)
            action = tr('files.log_expand' if collapsed else 'files.log_collapse')
            self.collapse_button.setToolTip(action)
            self.collapse_button.setAccessibleName(action)
            self.setMinimumHeight(COLLAPSED_HEIGHT if collapsed else 150)
            self.setMaximumHeight(COLLAPSED_HEIGHT if collapsed else 16777215)
        finally:
            self._updating = False
        if not collapsed and self.follow.isChecked():
            self._follow_changed(True)

    def _scroll_changed(self, value):
        if not self._updating and value < self.editor.verticalScrollBar().maximum():
            self.follow.setChecked(False)

    def _range_changed(self, _minimum, _maximum):
        if not self._updating and self.follow.isChecked():
            self._follow_changed(True)

    def _follow_changed(self, enabled):
        if enabled:
            self._updating = True
            try:
                scrollbar = self.editor.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
            finally:
                self._updating = False
