"""Desktop visual components; no file, network or application ownership."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from core.i18n import tr


STYLE = '''
QMainWindow, QWidget#workspace { background: #f5f6fa; color: #23283b; }
QWidget { color: #23283b; font-size: 12px; }
QWidget#sidebar { background: #ffffff; border-right: 1px solid #e5e7ee; }
QLabel { background: transparent; border: none; }
QLabel#brand { font-size: 19px; font-weight: 700; }
QLabel#eyebrow { color: #656d81; font-size: 11px; font-weight: 600; }
QLabel#heading { font-size: 27px; font-weight: 700; color: #20253a; }
QLabel#description, QLabel#muted { color: #70778b; }
QWidget#historyEmpty { background: #f0f1f6; border-radius: 10px; }
QLabel#emptyHint { color: #9299ab; font-size: 13px; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; }
QLabel#heroTitle { font-size: 28px; font-weight: 700; }
QLabel#metric { font-size: 20px; font-weight: 600; }
QFrame#card { background: #ffffff; border: 1px solid #e7e9f1; border-radius: 12px; }
QFrame#settingDivider { background: #edf0f5; border: none; }
QFrame#hero { background: #edeefe; border: 1px solid #dedff6; border-radius: 16px; }
QWidget#footer { background: #ffffff; border-top: 1px solid #e5e7ee; }
QLabel#state { color: #626a80; font-size: 11px; padding: 3px 6px; }
QLabel#state[changed="true"] { color: #965c13; background: #fff4dd; border-radius: 5px; }
QListWidget { background: transparent; border: none; outline: none; padding: 0; }
QListWidget::item { padding: 12px 10px; margin: 3px 0; border-radius: 8px; color: #596078; }
QListWidget::item:hover { background: #f3f4fa; }
QListWidget::item:selected { background: #eeedff; color: #5850c9; font-weight: 600; }
QListWidget::item:focus { border: 1px solid #918ae2; }
QPushButton { background: #ffffff; border: 1px solid #dde0e9; border-radius: 7px;
    padding: 9px 16px; font-weight: 500; min-height: 18px; }
QPushButton:hover { background: #f4f3ff; border-color: #a39ce5; }
QPushButton:pressed { background: #e9e6fc; }
QPushButton:focus { border: 2px solid #7971d9; padding: 8px 15px; }
QPushButton:disabled { background: #f5f6f9; border-color: #e8eaf0; color: #9ca2b2; }
QPushButton#primary { background: #6658cc; border-color: #6658cc; color: white; }
QPushButton#primary:hover { background: #5749b7; }
QPushButton#primary:disabled { background: #b9b3de; border-color: #b9b3de; }
QPushButton#home { text-align: left; border: none; background: transparent; padding: 12px 10px; }
QPushButton#home[active="true"] { background: #eeedff; color: #5850c9; font-weight: 600; }
QPushButton#home:hover { background: #e3e0f9; color: #463b9f; }
QPushButton#home:pressed { background: #d6d0f3; }
QPushButton#home:focus { border: 2px solid #7971d9; }
QPushButton#subtle { background: transparent; border-color: transparent; color: #70778b; }
QPushButton#subtle:hover { background: #eeedff; border-color: #b8b0e5; color: #493fa4; }
QPushButton#subtle:pressed { background: #ded9f6; border-color: #8d82d2; }
QPushButton#subtle:focus { border: 2px solid #7971d9; color: #493fa4; }
QPushButton#subtle:disabled, QPushButton#home:disabled { background: transparent; border-color: transparent; color: #9ca2b2; }
QPushButton#danger { color: #b24e5b; }
QPushButton#exit { text-align: left; padding: 12px 10px; background: #fafbfe; color: #596078; }
QPushButton#exit:hover { background: #fff0f1; border-color: #d99aa3; color: #9e3445; }
QPushButton#exit:pressed { background: #f7dde1; }
QPushButton#exit:focus { border: 2px solid #7971d9; padding: 11px 9px; }
QPushButton#fieldHelp { border: 1px solid #b9bfce; border-radius: 10px; color: #777f93;
    background: transparent; padding: 0; min-height: 0; font-size: 12px; font-weight: 600; }
QPushButton#fieldHelp:hover { color: #5850c9; border-color: #8c82d6; background: #eeedff; }
QPushButton#fieldHelp:pressed { background: #ded9f6; }
QPushButton#fieldHelp:focus { border: 2px solid #7971d9; padding: 0; }
QPushButton#deviceNotice { border: 1px solid #d9b66e; border-radius: 10px; color: #87540b;
    background: #fff4dd; padding: 0; min-height: 0; font-size: 12px; font-weight: 600; }
QPushButton#deviceNotice:hover { background: #ffe9bc; border-color: #b1812c; }
QPushButton#deviceNotice:focus { border: 2px solid #7971d9; padding: 0; }
QTabWidget::pane { border: 1px solid #e1e4ee; background: #ffffff; border-radius: 7px; }
QTabBar::tab { padding: 10px 14px; color: #656d81; border: none; background: transparent; }
QTabBar::tab:selected { color: #5850c9; border-bottom: 2px solid #7971d9; }
QTabBar::tab:hover { background: #eeedff; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit {
    background: #fafbfe; border: 1px solid #dfe3ee; border-radius: 6px;
    padding: 7px 10px; selection-background-color: #dcd7fa; selection-color: #272043; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
    border: 1px solid #887cda; background: #ffffff; }
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled, QSpinBox:disabled, QPlainTextEdit:disabled {
    color: #9399aa; background: #f3f4f8; }
QComboBox QAbstractItemView { background: white; color: #23283b; selection-background-color: #eeedff;
    selection-color: #5850c9; border: 1px solid #dddfea; }
QComboBox::drop-down { border: none; width: 25px; }
QComboBox::down-arrow { image: none; }
QDateEdit::drop-down { width: 22px; border: none; background: transparent; }
QDateEdit::down-arrow { image: none; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #eeedff; }
QCalendarWidget QToolButton { color: #493fa4; background: transparent; border: none; padding: 4px; }
QCalendarWidget QToolButton:hover { background: #dcd7fa; }
QCalendarWidget QAbstractItemView { background: #ffffff; selection-background-color: #6658cc;
    selection-color: #ffffff; outline: none; }
QCheckBox { spacing: 8px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #ccd0df; border-radius: 4px; min-height: 40px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QToolTip { background: #282c40; color: #ffffff; border: none; padding: 6px; }
'''


def apply_theme(window):
    window.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))
    window.setStyleSheet(STYLE)


def text(key, role=None):
    widget = QLabel(tr('gui.' + key))
    if role:
        widget.setObjectName(role)
    widget.setWordWrap(True)
    return widget


def card(layout, title=None, *, hero=False):
    frame = QFrame()
    frame.setObjectName('hero' if hero else 'card')
    box = QVBoxLayout(frame)
    box.setContentsMargins(22, 20, 22, 20)
    box.setSpacing(14)
    if title:
        box.addWidget(text(title, 'cardTitle'))
    layout.addWidget(frame)
    return box


class Toggle(QCheckBox):
    """A checkable switch retaining Qt keyboard and accessibility semantics."""

    def sizeHint(self):
        return QSize(48, 30)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = '#7769d5' if self.isChecked() else '#b8bfd0'
        if not self.isEnabled():
            color = '#d9dce5'
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(3, 4, 40, 22), 11, 11)
        painter.setBrush(QColor('#ffffff'))
        painter.drawEllipse(QRectF(25 if self.isChecked() else 6, 7, 16, 16))
        if self.hasFocus():
            painter.setPen(QPen(QColor('#5144b3'), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(0.8, 1.5, 45, 27), 13, 13)


class ScrollSafeInput:
    """Leave wheel gestures to the surrounding page, even with keyboard focus."""

    def wheelEvent(self, event):
        event.ignore()


class IntegerInput(ScrollSafeInput, QSpinBox):
    pass


class DecimalInput(ScrollSafeInput, QDoubleSpinBox):
    """Keep six-decimal editing precision without padding every value with zeros."""

    def textFromValue(self, value):
        return super().textFromValue(value).rstrip('0').rstrip(self.locale().decimalPoint())


class Choice(ScrollSafeInput, QComboBox):
    """Draw a consistent dropdown affordance across native and offscreen styles."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#70778b' if self.isEnabled() else '#b8bfd0'), 1.5))
        x, y = self.width() - 17, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))


class MicrophoneMark(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(64, 64)
        from .shell import desktop_icon
        self.icons = {state: desktop_icon(state) for state in (False, True)}
        self.recording = None
        self.update_recording(False)

    def update_recording(self, recording):
        if self.recording != recording:
            self.recording = recording
            self.setPixmap(self.icons[recording].pixmap(64, 64))


class HomePage(QWidget):
    def __init__(self, navigate, action, start):
        super().__init__()
        self.setObjectName('workspace')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(20)
        layout.addWidget(text('home_title', 'heading'))
        layout.addWidget(text('home_intro', 'description'))
        hero = card(layout, hero=True)
        hero_row = QHBoxLayout()
        hero_row.setSpacing(20)
        self.mark = MicrophoneMark()
        hero_row.addWidget(self.mark)
        summary = QVBoxLayout()
        self.headline = text('home_stopped', 'heroTitle')
        self.detail = text('home_start_hint', 'description')
        summary.addWidget(self.headline)
        summary.addWidget(self.detail)
        hero_row.addLayout(summary, 1)
        hero.addLayout(hero_row)
        controls = QHBoxLayout()
        self.start_button = QPushButton(tr('gui.retry_client'))
        self.start_button.setObjectName('primary')
        self.start_button.clicked.connect(start)
        controls.addWidget(self.start_button)
        self.pause_button = QPushButton(tr('gui.toggle_pause'))
        self.pause_button.clicked.connect(lambda: action('toggle_pause'))
        self.pause_button.setEnabled(False)
        controls.addWidget(self.pause_button)
        controls.addStretch()
        hero.addLayout(controls)
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.metrics = {}
        for key in ('asr_service', 'llm_processing'):
            box = card(metrics, key)
            value = text('not_running', 'metric')
            hint = text('home_unknown', 'muted')
            box.addWidget(value)
            box.addWidget(hint)
            self.metrics[key] = (value, hint)
        layout.addLayout(metrics)
        shortcuts = card(layout, 'shortcuts_title')
        self.shortcuts = text('home_unknown', 'metric')
        shortcuts.addWidget(self.shortcuts)
        shortcuts.addWidget(text('shortcuts_hint', 'muted'))
        links = QHBoxLayout()
        links.setSpacing(12)
        for title, page in (('tune_text', 1), ('page.records', 3), ('recent', 4)):
            button = QPushButton(tr('gui.' + title))
            button.clicked.connect(lambda _checked=False, page=page: navigate(page))
            links.addWidget(button)
        layout.addLayout(links)
        self.note = text('home_file_only', 'muted')
        layout.addWidget(self.note)
        layout.addStretch()

    def update_snapshot(self, snapshot):
        runtime = snapshot.get('runtime') or {}
        self.mark.update_recording(bool(runtime.get('recording')))
        config = (snapshot.get('effective') or snapshot.get('saved') or {}).get('ClientConfig', {})
        desktop = snapshot.get('desktop') or {}
        state = ('recording' if runtime.get('recording') else 'paused' if runtime.get('paused') else
                 'home_processing' if runtime.get('processing_count') else
                 'home_microphone' if runtime.get('microphone_ready') is False else
                 'home_ready' if runtime.get('connected') else 'home_connecting' if runtime else 'home_stopped')
        self.headline.setText(tr('gui.' + state))
        hint = ('recording_hint' if runtime.get('recording') else
                'processing_hint' if runtime.get('processing_count') else
                'home_ready_hint' if runtime.get('connected') else 'home_start_hint')
        self.detail.setText(tr('gui.' + hint))
        self.pause_button.setEnabled(bool(runtime))
        self.pause_button.setText(tr('gui.resume' if runtime.get('paused') else 'gui.pause'))
        self.start_button.setVisible(desktop.get('phase') == 'failed')
        self.start_button.setEnabled(desktop.get('phase', 'stopped') not in ('starting', 'stopping'))
        if desktop.get('phase') in ('starting', 'stopping', 'failed'):
            self.headline.setText(tr('gui.desktop_' + desktop['phase']))
            self.detail.setText(desktop.get('message') or '')
        elif runtime and not runtime.get('connected'):
            self.detail.setText(tr('gui.connection_hint'))
        elif runtime.get('paused'):
            self.detail.setText(tr('gui.paused_hint'))
        elif runtime.get('microphone_ready') is False:
            self.detail.setText(tr('gui.microphone_hint'))
        elif not runtime and not desktop:
            self.detail.setText(tr('gui.home_file_only'))
        service, service_hint = self.metrics['asr_service']
        service.setText(tr('gui.connected' if runtime.get('connected') else 'gui.not_connected'))
        service_hint.setText(str(config.get('addr', '')) + ':' + str(config.get('port', '')))
        llm, llm_hint = self.metrics['llm_processing']
        llm.setText(tr('state.on' if config.get('llm_enabled') else 'state.off'))
        llm_hint.setText(tr('gui.choice.' + config.get('llm_correction_level', 'natural')))
        shortcuts = []
        for row in config.get('shortcuts', []):
            if not row.get('enabled', True):
                continue
            key = str(row.get('key', ''))
            if key in ('ctrl_r', 'ctrl_l', 'x1', 'x2', 'caps_lock'):
                key = tr('gui.key.' + key)
            mode = tr('gui.shortcut_hold' if row.get('hold_mode', True) else 'gui.shortcut_toggle')
            shortcuts.append(f'{key} · {mode}')
        self.shortcuts.setText('  /  '.join(shortcuts) or tr('gui.none_shortcuts'))
        self.note.setText(tr('gui.home_running' if runtime else 'gui.home_file_only'))


GROUP_STARTS = {
    'ui_language': 'group.interface', 'language': 'group.microphone',
    'llm_enabled': 'group.llm', 'llm_correction_level': 'group.correction',
    'caret_context_enabled': 'group.context', 'addr': 'group.connection',
    'paste': 'group.output', 'save_transcripts': 'group.history', 'save_audio': 'group.audio',
    'save_diagnostic_logs': 'group.diagnostics',
}
