"""Compact field help and width-aware settings groups, owned by the Qt thread."""

from html import escape

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolTip, QVBoxLayout, QWidget,
)

from core.i18n import tr


class HelpButton(QPushButton):
    """Native hover tooltip with click and keyboard access to the same explanation."""

    def __init__(self, name):
        super().__init__('?')
        title = tr('gui.' + name)
        self.field_name = name
        explanation = tr('gui.help.' + name)
        self.setObjectName('fieldHelp')
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_explanation(title, explanation)
        self.setToolTipDuration(20000)
        self.clicked.connect(self.show_help)

    def set_explanation(self, title, explanation):
        self.setAccessibleName(tr('gui.help_for', name=title))
        self.setAccessibleDescription(explanation)
        self.setToolTip('<qt><table width="320"><tr><td><b>' + escape(title)
                        + '</b></td></tr><tr><td>' + escape(explanation) + '</td></tr></table></qt>')

    def show_help(self):
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height() + 4)), self.toolTip(), self)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F1:
            self.show_help()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            QToolTip.hideText()
            event.accept()
        else:
            super().keyPressEvent(event)


class DeviceNotice(HelpButton):
    """Persistent error detail in a fixed inline slot, never a transient loading row."""

    def __init__(self):
        super().__init__('input_device')
        self.setObjectName('deviceNotice')
        self.setText('!')
        policy = self.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.setSizePolicy(policy)
        self.set_notice(None)

    def set_notice(self, message):
        self.set_explanation(tr('gui.device_status'), message or '')
        self.setVisible(bool(message))


class FieldLabel(QLabel):
    """Prefer one line when space permits, while still wrapping in narrow rows."""

    def __init__(self, value):
        super().__init__(value)
        self.setMaximumWidth(self.sizeHint().width())

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 2, self.fontMetrics().height())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.setMaximumWidth(self.sizeHint().width())

    def showEvent(self, event):
        super().showEvent(event)
        self.setMaximumWidth(self.sizeHint().width())


def field_caption(name, widget):
    """Keep the help affordance next to its label, including when labels wrap."""
    caption = QWidget()
    caption.setObjectName('fieldCaption')
    caption.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
    row = QHBoxLayout(caption)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    label = FieldLabel(tr('gui.' + name))
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setBuddy(widget)
    row.addWidget(label)
    row.addWidget(HelpButton(name))
    row.addStretch()
    widget.setAccessibleDescription(tr('gui.help.' + name))
    return caption


class SettingsGroups(QWidget):
    """Use two columns when cards have room, retaining widgets and editing state."""

    def __init__(self, minimum_column_width=460, equal_height=False):
        super().__init__()
        self.cards = []
        self.columns = 1
        self.minimum_column_width = minimum_column_width
        self.card_alignment = Qt.AlignmentFlag(0) if equal_height else Qt.AlignmentFlag.AlignTop
        self.viewport = None
        self.horizontal_margin = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(16)
        if equal_height:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)

    def add_group(self, title):
        frame = QFrame()
        frame.setObjectName('card')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        label = QLabel(tr('gui.' + title))
        label.setObjectName('cardTitle')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.cards.append(frame)
        self.grid.addWidget(frame, len(self.cards) - 1, 0, self.card_alignment)
        return layout

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def bind_viewport(self, viewport, horizontal_margin):
        self.viewport = viewport
        self.horizontal_margin = horizontal_margin
        viewport.installEventFilter(self)
        self.reflow()

    def eventFilter(self, watched, event):
        if watched is self.viewport and event.type() == QEvent.Type.Resize:
            self.reflow()
        return super().eventFilter(watched, event)

    def reflow(self):
        # The current two-column minimum can exceed a shrinking viewport. Use the
        # viewport's available width so that minimum cannot lock the layout wide.
        width = self.viewport.width() - self.horizontal_margin if self.viewport else self.width()
        columns = 2 if width >= self.minimum_column_width * 2 and len(self.cards) > 1 else 1
        if columns == self.columns:
            return
        self.columns = columns
        for frame in self.cards:
            self.grid.removeWidget(frame)
        for index, frame in enumerate(self.cards):
            self.grid.addWidget(frame, index // columns, index % columns, self.card_alignment)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if columns == 2 else 0)
