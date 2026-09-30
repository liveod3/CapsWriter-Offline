"""Per-batch file options, separate from microphone and server configuration."""

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.i18n import localize_notice, tr
from core.settings_gui.choices import RecognitionChoice
from core.settings_gui.presentation import DecimalInput, IntegerInput, apply_theme
from .options import DEFAULTS, RANGES, validate_options


def settings_icon():
    """Paint a scale-independent adjustment icon without platform font glyphs."""
    pixmap = QPixmap(48, 48)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#6658cc'), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for y, x in ((6, 9), (12, 16), (18, 7)):
        painter.drawLine(QPointF(3, y), QPointF(21, y))
        painter.setBrush(QColor('#eeedff'))
        painter.drawEllipse(QPointF(x, y), 2.4, 2.4)
    painter.end()
    return QIcon(pixmap)


STYLE = '''
QDialog { background: #f5f6fa; }
QFrame#stepper { background: #fafbfe; border: 1px solid #dfe3ee; border-radius: 9px; }
QFrame#stepper QAbstractSpinBox { border: none; background: transparent; padding: 6px 2px; }
QPushButton#stepButton { padding: 0; min-height: 0; border: none; background: transparent;
    color: #6658cc; font-size: 21px; border-radius: 6px; }
QPushButton#stepButton:hover { background: #eeedff; }
QPushButton#stepButton:focus { border: 1px solid #887cda; }
QPushButton#stepButton:disabled { color: #c1c5d1; }
QLabel#error { color: #b24e5b; }
'''


class NumberStepper(QFrame):
    """Expose large minus/plus targets while retaining native numeric editing."""

    def __init__(self, control, name):
        super().__init__()
        self.setObjectName('stepper')
        self.setFixedSize(210, 42)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(0)
        control.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        control.setAlignment(Qt.AlignmentFlag.AlignCenter)
        control.setAccessibleName(name)
        self.setFocusProxy(control)
        self.decrease = QPushButton('−')
        self.increase = QPushButton('+')
        for button, key, step in ((self.decrease, 'decrease', -1), (self.increase, 'increase', 1)):
            button.setObjectName('stepButton')
            button.setFixedSize(32, 32)
            button.setAutoDefault(False)
            button.setAutoRepeat(True)
            button.setAccessibleName(tr('files.' + key, name=name))
            button.setToolTip(button.accessibleName())
            button.clicked.connect(lambda _checked=False, delta=step: control.stepBy(delta))
        layout.addWidget(self.decrease)
        layout.addWidget(control, 1)
        layout.addWidget(self.increase)
        QWidget.setTabOrder(self.decrease, control)
        QWidget.setTabOrder(control, self.increase)

        def update_buttons():
            self.decrease.setEnabled(control.value() > control.minimum())
            self.increase.setEnabled(control.value() < control.maximum())

        control.valueChanged.connect(update_buttons)
        update_buttons()


class FileOptionsDialog(QDialog):
    def __init__(self, values, endpoint, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('files.settings'))
        self.setMinimumWidth(660)
        self.resize(700, 760)
        apply_theme(self)
        self.setStyleSheet(self.styleSheet() + STYLE)
        self.values = dict(values)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        heading = QLabel(tr('files.settings'))
        heading.setObjectName('heading')
        layout.addWidget(heading)
        note = QLabel(tr('files.settings_hint'))
        note.setObjectName('muted')
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName('workspace')
        sections = QVBoxLayout(body)
        sections.setContentsMargins(0, 0, 8, 0)
        sections.setSpacing(12)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        def section(key):
            card = QFrame()
            card.setObjectName('card')
            box = QVBoxLayout(card)
            box.setContentsMargins(18, 16, 18, 16)
            box.setSpacing(14)
            title = QLabel(tr('files.' + key))
            title.setObjectName('cardTitle')
            box.addWidget(title)
            sections.addWidget(card)
            return box

        def row(box, key, control, hint=None):
            line = QHBoxLayout()
            text = QVBoxLayout()
            title = QLabel(tr('files.option.' + key))
            title.setBuddy(control)
            text.addWidget(title)
            if hint:
                description = QLabel(tr('files.' + hint))
                description.setObjectName('muted')
                description.setWordWrap(True)
                text.addWidget(description)
            line.addLayout(text, 1)
            line.addSpacing(20)
            line.addWidget(control)
            box.addLayout(line)

        self.fields = {}
        general = section('settings_general')
        language = RecognitionChoice()
        language.set_config_value(values.get('language', DEFAULTS['language']))
        self.fields['language'] = language
        language.setFixedWidth(210)
        row(general, 'language', language, 'language_hint')
        audio = section('settings_audio')
        timeouts = None
        self.steppers = {}
        for key, (lower, upper) in RANGES.items():
            if key == 'file_io_timeout':
                timeouts = section('settings_timeouts')
            control = IntegerInput() if key == 'file_max_inflight_chunks' else DecimalInput()
            value = values.get(key, DEFAULTS[key])
            # Preserve existing out-of-range values for explicit validation/repair.
            numeric = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else DEFAULTS[key]
            if isinstance(control, IntegerInput):
                numeric = int(numeric)
            control.setRange(min(lower, numeric), max(upper, numeric))
            if isinstance(control, DecimalInput):
                control.setDecimals(3)
                control.setSuffix(tr('files.seconds_suffix'))
            control.setValue(numeric)
            control.setToolTip(tr('files.help.' + key))
            self.fields[key] = control
            stepper = NumberStepper(control, tr('files.option.' + key))
            self.steppers[key] = stepper
            row(timeouts if timeouts is not None else audio, key, stepper, 'brief.' + key)
        connection_box = section('settings_connection')
        connection = QLabel(tr('files.endpoint', endpoint=endpoint))
        connection.setTextFormat(Qt.TextFormat.PlainText)
        connection.setWordWrap(True)
        connection_box.addWidget(connection)
        server_note = QLabel(tr('files.server_settings_hint'))
        server_note.setObjectName('muted')
        server_note.setWordWrap(True)
        connection_box.addWidget(server_note)
        sections.addStretch()
        self.error = QLabel()
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.restore_button = buttons.addButton(tr('files.restore_defaults'), QDialogButtonBox.ButtonRole.ResetRole)
        self.restore_button.setAutoDefault(False)
        self.restore_button.clicked.connect(self.restore_defaults)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName('primary')
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr('files.apply_settings'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('files.cancel_settings'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def restore_defaults(self):
        """Stage factory values; only Apply commits them to this window's batch."""
        for key, widget in self.fields.items():
            if key == 'language':
                widget.set_config_value(DEFAULTS[key])
            else:
                widget.setRange(*RANGES[key])
                widget.setValue(DEFAULTS[key])
        self.error.clear()

    def accept(self):
        candidate = {key: widget.currentData() if key == 'language' else widget.value()
                     for key, widget in self.fields.items()}
        try:
            self.values = validate_options(candidate)
        except ValueError as exc:
            self.error.setText(str(localize_notice(exc.args[0])))
            return
        super().accept()
