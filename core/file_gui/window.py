"""File queue and explicit import gestures, entirely on the Qt main thread."""

from dataclasses import dataclass, field
from pathlib import Path
import math
import time

from PySide6.QtCore import Qt, QTimer, QUrl, QSize, QItemSelectionModel
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QListWidgetItem, QMainWindow, QPushButton, QMessageBox,
    QSizePolicy, QVBoxLayout, QWidget,
)

from core.i18n import localize_notice, tr
from core.settings_gui.presentation import apply_theme
from core.file_progress import ProgressSnapshot, format_duration
from .log_view import FileEventLog
from .progress_bar import FileProgressBar
from .options import DEFAULTS, validate_options
from .settings import FileOptionsDialog, settings_icon
from .imports import FileImportWorker
from .picker import choose_paths
from .queue_view import FileQueueList, QueueCheckBox, QueueMenu


MAX_FILES = 200
DEFAULT_EXTENSIONS = frozenset({
    '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.wma', '.mp4',
    '.mkv', '.mov', '.avi', '.flv', '.webm', '.m4v', '.ts',
})
EXTRA_STYLE = '''
QFrame#dropZone { background: #f0effc; border: 2px dashed #c3bdeb; border-radius: 14px; }
QFrame#dropZone[dragging="true"] { background: #e4dffc; border: 2px solid #7969d9; }
QLabel#dropTitle { font-size: 20px; font-weight: 600; color: #493fa4; }
QLabel#fileKind { color: #6658cc; background: #f0effc; border-radius: 8px; font-weight: 600; }
QLabel#fileName { font-size: 13px; font-weight: 600; }
QLabel#fileState { color: #747b8e; }
QLabel#fileState[state="completed"] { color: #26805b; }
QLabel#fileState[state="failed"] { color: #b24e5b; }
QLabel#fileState[state="running"] { color: #6658cc; }
QListWidget#fileQueue::item { padding: 0; margin: 2px 0; border-radius: 9px; }
QPushButton#settingsButton { background: #eeedff; border-color: #d9d3f7; color: #493fa4; }
QPushButton#settingsButton:hover { background: #e4dffc; border-color: #a39ce5; }
QPushButton#queueSelect { background: transparent; border: 1px solid transparent; color: #70778b; padding: 4px 8px; }
QPushButton#queueSelect:hover { background: #f4f2fc; color: #6658aa; }
QPushButton#queueSelect:checked { background: transparent; color: #6658aa; }
QPushButton#queueSelect:focus { border: 1px dotted #aba1d2; }
QPushButton#queueSelect:disabled { color: #9ca2b2; }
QPushButton#queueClear { background: transparent; border-color: transparent; color: #a83245; }
QPushButton#queueClear:hover { background: #fff0f1; border-color: #d99aa3; }
QPushButton#queueClear:disabled { color: #9ca2b2; background: transparent; border-color: transparent; }
QMenu { background: white; border: 1px solid #e1deef; padding: 6px; }
QMenu::item { padding: 8px 24px; border-radius: 5px; }
QMenu::item:selected { background: #eeedff; color: #493fa4; }
QMenu::item:disabled { color: #9ca2b2; }
QMenu::separator { height: 1px; background: #edf0f5; margin: 5px; }
'''


def label(key, role=None, **values):
    widget = QLabel(tr('files.' + key, **values))
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if role:
        widget.setObjectName(role)
    return widget


def button(key, callback, role=None):
    widget = QPushButton(tr('files.' + key))
    if role:
        widget.setObjectName(role)
    widget.clicked.connect(callback)
    return widget


def local_paths(mime):
    """Accept filesystem URLs only; remote URLs never become network requests."""
    if mime is None or not mime.hasUrls():
        return []
    return [url.toLocalFile() for url in mime.urls()[:MAX_FILES + 1] if url.isLocalFile()]


class ElidedLabel(QLabel):
    """Keep full user paths in tooltips while allowing narrow queue rows."""

    def __init__(self, value, role):
        super().__init__()
        self.value = value
        self.setObjectName(role)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip(value)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event):
        self.setText(self.fontMetrics().elidedText(self.value, Qt.TextElideMode.ElideMiddle, self.width()))
        super().resizeEvent(event)


@dataclass
class QueueEntry:
    path: Path
    state: str = 'pending'
    metrics: dict = field(default_factory=dict)
    failure: str = ''
    outputs: tuple[Path, ...] = ()


def duration(seconds):
    if seconds is None:
        return '--:--'
    return format_duration(seconds)


def failure_text(code):
    known = {'recognition_failed', 'decode_failed', 'missing_file', 'decoder_unavailable',
             'connection_failed', 'timeout', 'invalid_result', 'output_failed', 'unexpected'}
    if code in known:
        return tr('file.failure.' + code + '.reason') + ' ' + tr('file.failure.' + code + '.action')
    return tr('files.error.' + code) if code in {
        'config_invalid', 'start_failed', 'startup_timeout', 'protocol_error', 'shutdown_timeout', 'failed',
    } else tr('files.error.start_failed')


class FileTranscriptionWindow(QMainWindow):
    def __init__(self, runner, config=None):
        super().__init__()
        self.runner = runner
        self.config = config or {}
        self.entries = []
        self.current = None
        self.batch = []
        self.batch_formats = frozenset()
        self.stopping = False
        self.pause_requested = False
        self.paused = False
        self.confirming_abort = False
        self.closing = False
        self.busy = False
        self.importer = FileImportWorker(self)
        self.importer.finished.connect(self.import_finished)
        self.options = {key: self.config.get(key, default) for key, default in DEFAULTS.items()}
        self.batch_options = {}
        self.row_labels = []
        self.started_at = None
        self.last_event_at = None
        self.last_completed = None
        self.batch_entries = []
        self.batch_started_at = None
        self.logged_stages = set()
        self.logged_duration = False
        self.last_logged_processed = 0.0
        scheme = 'wss' if self.config.get('use_tls', False) else 'ws'
        self.endpoint = f"{scheme}://{self.config.get('addr', '127.0.0.1')}:{self.config.get('port', '6016')}"
        configured = self.config.get('file_media_extensions', DEFAULT_EXTENSIONS)
        if not isinstance(configured, (tuple, list, set, frozenset)):
            configured = DEFAULT_EXTENSIONS
        self.extensions = frozenset('.' + str(ext).lower().lstrip('.') for ext in configured) or DEFAULT_EXTENSIONS
        self.setWindowTitle(tr('files.window_title'))
        self.resize(1280, 860)
        self.setMinimumSize(1060, 780)
        apply_theme(self)
        self.setStyleSheet(self.styleSheet() + EXTRA_STYLE)
        self.setAcceptDrops(True)

        workspace = QWidget()
        workspace.setObjectName('workspace')
        self.setCentralWidget(workspace)
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(30, 24, 30, 22)
        layout.setSpacing(12)
        masthead = QHBoxLayout()
        brand = QLabel('CapsWriter')
        brand.setObjectName('brand')
        masthead.addWidget(brand)
        masthead.addSpacing(18)
        masthead.addWidget(label('title', 'heading'))
        masthead.addStretch()
        self.settings_button = button('settings', self.edit_options, 'settingsButton')
        self.settings_button.setIcon(settings_icon())
        self.settings_button.setIconSize(QSize(20, 20))
        self.settings_button.setToolTip(tr('files.settings_hint'))
        masthead.addWidget(self.settings_button)
        layout.addLayout(masthead)
        self.drop_zone = QFrame()
        self.drop_zone.setObjectName('dropZone')
        drop = QHBoxLayout(self.drop_zone)
        drop.setContentsMargins(20, 14, 20, 14)
        drop.setSpacing(14)
        drop_text = QVBoxLayout()
        drop_text.setSpacing(6)
        self.drop_title = label('drop_title', 'dropTitle')
        self.drop_hint = label('drop_hint', 'muted')
        for widget in (self.drop_title, self.drop_hint):
            drop_text.addWidget(widget)
        drop.addLayout(drop_text, 1)
        import_buttons = QHBoxLayout()
        self.add_button = button('choose', self.choose_files)
        self.add_button.setToolTip(tr('files.choose_hint'))
        self.paste_button = button('paste', self.paste_files)
        import_buttons.addWidget(self.add_button)
        import_buttons.addWidget(self.paste_button)
        drop.addLayout(import_buttons)
        layout.addWidget(self.drop_zone)

        content = QHBoxLayout()
        content.setSpacing(18)
        queue_card = self.queue_card = QFrame()
        queue_card.setObjectName('card')
        queue = QVBoxLayout(queue_card)
        queue.setContentsMargins(18, 16, 18, 12)
        heading = QHBoxLayout()
        heading.addWidget(label('queue', 'cardTitle'))
        self.count = label('count', 'muted', count=0)
        heading.addStretch()
        heading.addWidget(self.count)
        self.select_button = QPushButton(tr('files.selection_mode'))
        self.select_button.setObjectName('queueSelect')
        self.select_button.setCheckable(True)
        self.select_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.select_button.setToolTip(tr('files.selection_hint'))
        self.select_button.toggled.connect(self.toggle_selection_mode)
        heading.addWidget(self.select_button)
        queue.addLayout(heading)
        self.queue_hint = label('queue_hint', 'muted')
        queue.addWidget(self.queue_hint)
        self.empty = label('empty', 'emptyHint')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        queue.addWidget(self.empty, 1)
        self.file_list = FileQueueList()
        self.file_list.setMinimumHeight(180)
        self.file_list.setObjectName('fileQueue')
        self.file_list.setAccessibleName(tr('files.queue'))
        self.file_list.move_requested.connect(self.move_rows)
        self.file_list.remove_requested.connect(self.remove_selected)
        self.file_list.step_requested.connect(self.move_selected)
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self.queue_menu)
        self.file_list.currentRowChanged.connect(lambda _: self.update_controls())
        self.file_list.currentRowChanged.connect(lambda _: self.show_selected_detail())
        self.file_list.itemSelectionChanged.connect(self.update_controls)
        queue.addWidget(self.file_list, 1)
        queue_actions = QHBoxLayout()
        self.clear_button = button('clear', self.clear_queue, 'queueClear')
        self.open_button = button('open_folder', self.open_folder, 'subtle')
        queue_actions.addWidget(self.clear_button)
        queue_actions.addStretch()
        queue_actions.addWidget(self.open_button)
        queue.addLayout(queue_actions)
        self.detail = label('select_detail', 'muted')
        self.detail.hide()
        queue.addWidget(self.detail)
        content.addWidget(queue_card, 5)

        self.side_panel = QWidget()
        self.side_panel.setMinimumWidth(400)
        self.side_panel.setMaximumWidth(580)
        side = self.side_layout = QVBoxLayout(self.side_panel)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(12)
        content.addWidget(self.side_panel, 4)
        layout.addLayout(content, 1)

        options_card = QFrame()
        options_card.setObjectName('card')
        options = QVBoxLayout(options_card)
        options.setContentsMargins(18, 14, 18, 14)
        options.setSpacing(10)
        options.addWidget(label('export', 'cardTitle'))
        format_grid = QGridLayout()
        self.formats = {}
        for index, name in enumerate(('srt', 'txt', 'json', 'merge')):
            checkbox = QCheckBox(tr('files.format_' + name))
            checkbox.setChecked(name == 'srt')
            checkbox.toggled.connect(lambda _: self.update_controls())
            self.formats[name] = checkbox
            format_grid.addWidget(checkbox, index // 2, index % 2)
        options.addLayout(format_grid)
        options.addWidget(label('destination', 'muted'))
        side.addWidget(options_card)

        status_card = self.status_card = QFrame()
        status_card.setObjectName('card')
        status_box = QVBoxLayout(status_card)
        status_box.setContentsMargins(18, 14, 18, 14)
        progress_heading = QHBoxLayout()
        self.stage = label('state.idle', 'cardTitle')
        self.stage.setWordWrap(False)
        self.percentage = label('no_progress', 'muted')
        progress_heading.addWidget(self.stage)
        progress_heading.addStretch()
        progress_heading.addWidget(self.percentage)
        status_box.addLayout(progress_heading)
        self.idle_hint = label('idle_hint', 'muted')
        status_box.addWidget(self.idle_hint)
        self.processing_details = QWidget()
        details = QVBoxLayout(self.processing_details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(8)
        self.active_name = ElidedLabel('', 'fileName')
        details.addWidget(self.active_name)
        self.phase = label('stage.starting', 'muted')
        details.addWidget(self.phase)
        self.audio_metrics = label('audio_metrics_empty', 'muted')
        self.audio_metrics.setToolTip(tr('files.audio_metrics_help'))
        details.addWidget(self.audio_metrics)
        self.progress = FileProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(18)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.metrics = label('metrics_empty', 'muted')
        self.metrics.setToolTip(tr('files.metrics_help'))
        details.addWidget(self.metrics)
        self.chunk_metrics = label('speed_metrics_empty', 'muted')
        details.addWidget(self.chunk_metrics)
        self.chunk_metrics.setToolTip(tr('files.metrics_help'))
        details.addWidget(self.progress)
        status_box.addWidget(self.processing_details)
        side.addWidget(status_card)
        self.event_log = FileEventLog()
        side.addWidget(self.event_log, 1)
        side.addStretch(0)
        self.event_log.collapse_button.toggled.connect(self.log_collapsed)
        footer = QHBoxLayout()
        self.notice = label('ready', 'muted')
        self.notice.setMinimumWidth(0)
        footer.addWidget(self.notice, 1)
        self.stop_button = button('abort_pause', self.confirm_stop, 'danger')
        self.stop_button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.pause_button = button('pause_after_file', self.toggle_pause)
        self.start_button = button('start', self.start_batch, 'primary')
        self.start_button.setMinimumWidth(132)
        footer.addWidget(self.stop_button)
        footer.addWidget(self.pause_button)
        footer.addWidget(self.start_button)
        layout.addLayout(footer)
        self.paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self)
        self.paste_shortcut.activated.connect(self.paste_files)
        self.open_shortcut = QShortcut(QKeySequence.StandardKey.Open, self)
        self.open_shortcut.activated.connect(self.choose_files)
        runner.finished.connect(self.file_finished)
        runner.progress.connect(self.on_progress)
        runner.outputs.connect(self.on_outputs)
        self.clock = QTimer(self)
        self.clock.setInterval(125)
        self.clock.timeout.connect(self.update_metrics)
        self.clock.start()
        self.update_controls()

    def set_notice(self, key, **values):
        self.notice.setText(tr('files.' + key, **values))

    def log_collapsed(self, collapsed):
        self.side_layout.setStretch(2, 0 if collapsed else 1)
        self.side_layout.setStretch(3, 1 if collapsed else 0)

    def update_status_visibility(self):
        self.stage.setText(tr('files.state.processing' if self.busy else 'files.state.idle'))
        self.idle_hint.setText(tr('files.paused_hint' if self.paused else 'files.idle_hint'))
        self.idle_hint.setVisible(not self.busy)
        self.processing_details.setVisible(self.busy)
        self.percentage.setVisible(self.busy)
        self.status_card.setMinimumHeight(self.status_card.minimumSizeHint().height())

    def show_drag(self, active):
        self.drop_zone.setProperty('dragging', active)
        self.drop_zone.style().unpolish(self.drop_zone)
        self.drop_zone.style().polish(self.drop_zone)
        self.drop_title.setText(tr('files.release' if active else 'files.drop_title'))
        self.drop_hint.setText(tr('files.release_hint' if active else 'files.drop_hint'))

    def dragEnterEvent(self, event):
        if local_paths(event.mimeData()) and not self.closing and not self.importer.active:
            self.show_drag(True)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if local_paths(event.mimeData()) and not self.closing and not self.importer.active:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.show_drag(False)
        event.accept()

    def dropEvent(self, event):
        self.show_drag(False)
        paths = local_paths(event.mimeData())
        if paths and not self.closing and not self.importer.active:
            self.add_paths(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def choose_files(self):
        if self.closing or self.importer.active:
            return
        try:
            paths = choose_paths(self, self.extensions)
        except RuntimeError:
            self.set_notice('picker_unavailable')
            self.event_log.append(tr('files.picker_unavailable'), 'error')
            return
        if paths:
            self.add_paths(paths)

    def paste_files(self):
        if self.closing or self.importer.active:
            return
        # Read clipboard data only in response to the explicit button/shortcut.
        mime = QApplication.clipboard().mimeData()
        paths = local_paths(mime)
        if not paths and mime is not None and mime.hasText():
            paths = [line.strip().strip('"') for line in mime.text()[:65536].splitlines()[:MAX_FILES + 1]
                     if line.strip()]
        self.add_paths(paths)

    def add_paths(self, paths):
        if self.closing or self.importer.active:
            return
        if self.importer.start(paths, [entry.path for entry in self.entries], self.extensions,
                               recursive=bool(self.config.get('file_scan_recursive', True)), capacity=MAX_FILES):
            self.set_notice('scanning')
            self.update_controls()

    def import_finished(self, result):
        if self.closing:
            if not self.busy:
                self.close()
            return
        if not result.cancelled:
            self.entries.extend(QueueEntry(path) for path in result.paths)
        self.refresh_queue()
        message = tr('files.scan_result', added=len(result.paths), skipped=result.skipped, errors=result.errors)
        self.notice.setText(message)
        self.event_log.append(message, 'warning' if result.errors else 'info')
        if result.limited:
            self.set_notice('scan_limited')
            self.event_log.append(tr('files.scan_limited'), 'warning')

    def refresh_queue(self):
        selected = self.file_list.currentRow()
        selected_paths = {item.data(Qt.ItemDataRole.UserRole) for item in self.file_list.selectedItems()}
        current = self.file_list.currentItem()
        current_path = current.data(Qt.ItemDataRole.UserRole) if current else None
        scroll = self.file_list.verticalScrollBar().value()
        self.file_list.blockSignals(True)
        self.file_list.clear()
        self.row_labels = []
        for entry in self.entries:
            item = QListWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            if self.paused and entry.state == 'completed':
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            item.setData(Qt.ItemDataRole.UserRole, str(entry.path))
            item.setToolTip(str(entry.path))
            item.setData(Qt.ItemDataRole.AccessibleTextRole, entry.path.name + ' ' + tr('files.' + entry.state))
            row = QWidget(self.file_list)
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(4, 6, 4, 6)
            check = QueueCheckBox()
            check.setObjectName('queueCheck')
            check.setFixedSize(24, 24)
            check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            check.setAccessibleName(tr('files.select_file', name=entry.path.name))
            check.toggled.connect(lambda checked, target=item: target.setSelected(checked))
            check.setVisible(self.file_list.check_mode)
            row_layout.addWidget(check)
            kind = QLabel(entry.path.suffix.lstrip('.').upper())
            kind.setObjectName('fileKind')
            kind.setAlignment(Qt.AlignmentFlag.AlignCenter)
            kind.setFixedSize(48, 46)
            row_layout.addWidget(kind)
            names = QVBoxLayout()
            names.setSpacing(4)
            names.addWidget(ElidedLabel(entry.path.name, 'fileName'))
            names.addWidget(ElidedLabel(str(entry.path.parent), 'muted'))
            row_layout.addLayout(names, 1)
            state = label(entry.state, 'fileState')
            state.setProperty('state', entry.state)
            self.row_labels.append(state)
            row_layout.addWidget(state)
            row.ensurePolished()
            item.setSizeHint(QSize(0, max(68, row.sizeHint().height())))
            self.file_list.addItem(item)
            self.file_list.setItemWidget(item, row)
        if self.entries:
            index = next((i for i, entry in enumerate(self.entries) if str(entry.path) == current_path),
                         max(0, min(selected, len(self.entries) - 1)))
            self.file_list.setCurrentRow(index, QItemSelectionModel.SelectionFlag.NoUpdate
                                         if self.file_list.check_mode else QItemSelectionModel.SelectionFlag.ClearAndSelect)
            retained = selected_paths & {str(entry.path) for entry in self.entries}
            if retained or self.file_list.check_mode:
                for i, entry in enumerate(self.entries):
                    self.file_list.item(i).setSelected(str(entry.path) in retained)
        self.file_list.blockSignals(False)
        self.file_list.sync_checks()
        self.file_list.verticalScrollBar().setValue(scroll)
        self.update_controls()
        self.show_selected_detail()

    def update_controls(self):
        if self.paused and not any(entry.state != 'completed' for entry in self.entries):
            self.paused = False
        self.update_status_visibility()
        selected = 0 <= self.file_list.currentRow() < len(self.entries)
        self.empty.setVisible(not self.entries)
        self.file_list.setVisible(bool(self.entries))
        self.count.setText(tr('files.selection_count', selected=len(self.file_list.selectedItems()), total=len(self.entries))
                           if self.file_list.check_mode else tr('files.count', count=len(self.entries)))
        self.select_button.setEnabled(bool(self.entries) and not self.importer.active and not self.closing)
        self.select_button.setText(tr('files.selection_done' if self.file_list.check_mode else 'files.selection_mode'))
        self.start_button.setEnabled(not self.busy and not self.importer.active
                                     and any(entry.state != 'completed' for entry in self.entries)
                                     and any(box.isChecked() for box in self.formats.values()))
        self.stop_button.setVisible(self.busy)
        self.stop_button.setEnabled(self.busy and self.current is not None
                                   and not self.stopping and not self.confirming_abort)
        self.pause_button.setVisible(self.busy)
        self.pause_button.setEnabled(self.busy and not self.stopping and not self.confirming_abort)
        self.pause_button.setText(tr('files.cancel_pause' if self.pause_requested else 'files.pause_after_file'))
        self.start_button.setVisible(not self.busy)
        self.start_button.setText(tr('files.resume' if self.paused else 'files.start'))
        self.file_list.editable = self.queue_editable()
        self.queue_hint.setText(tr('files.queue_locked' if self.busy else
                                   'files.selection_hint' if self.file_list.check_mode else
                                   'files.queue_paused_hint' if self.paused else 'files.queue_hint'))
        self.file_list.setDragEnabled(self.file_list.editable and not self.file_list.check_mode)
        self.clear_button.setEnabled(bool(self.entries) and self.queue_editable())
        for control in (self.add_button, self.paste_button):
            control.setEnabled(not self.importer.active and not self.closing)
        self.open_button.setEnabled(selected)
        self.settings_button.setEnabled(not self.busy)
        for checkbox in self.formats.values():
            checkbox.setEnabled(not self.busy)

    def toggle_selection_mode(self, enabled):
        self.file_list.set_check_mode(enabled)
        self.update_controls()

    def remove_selected(self, paths=None):
        if self.queue_editable():
            if paths is None:
                paths = {str(self.entries[index].path) for index in self.selected_rows()}
            self.entries[:] = [entry for entry in self.entries if str(entry.path) not in paths]
            self.refresh_queue()

    def queue_editable(self):
        return not self.busy and not self.importer.active and not self.closing

    def selected_rows(self):
        return sorted(self.file_list.row(item) for item in self.file_list.selectedItems())

    def movable_rows(self):
        return [i for i, entry in enumerate(self.entries) if not self.paused or entry.state != 'completed']

    def move_rows(self, rows, destination):
        if not self.queue_editable() or not rows:
            return
        rows = sorted(set(rows))
        if rows[0] < 0 or rows[-1] >= len(self.entries):
            return
        destination = max(0, min(len(self.entries), destination))
        movable = self.movable_rows()
        rows = [i for i in rows if i in movable]
        moving = [self.entries[i] for i in rows]
        remaining = [self.entries[i] for i in movable if i not in rows]
        insertion = sum(i < destination and i not in rows for i in movable)
        reordered = remaining[:insertion] + moving + remaining[insertion:]
        for index, entry in zip(movable, reordered):
            self.entries[index] = entry
        self.refresh_queue()

    def move_selected(self, direction):
        if not self.queue_editable():
            return
        movable = self.movable_rows()
        entries = [self.entries[i] for i in movable]
        rows = {movable.index(i) for i in self.selected_rows() if i in movable}
        for index in sorted(rows, reverse=direction > 0):
            neighbor = index + direction
            if 0 <= neighbor < len(entries) and neighbor not in rows:
                entries[index], entries[neighbor] = entries[neighbor], entries[index]
                rows.remove(index)
                rows.add(neighbor)
        for index, entry in zip(movable, entries):
            self.entries[index] = entry
        self.refresh_queue()

    def sort_queue(self, reverse=False):
        if self.queue_editable():
            movable = self.movable_rows()
            ordered = sorted((self.entries[i] for i in movable), key=lambda entry: entry.path.name.casefold(), reverse=reverse)
            for index, entry in zip(movable, ordered):
                self.entries[index] = entry
            self.refresh_queue()

    def build_queue_menu(self):
        menu = QueueMenu(self)
        rows = self.selected_rows()
        editable = self.queue_editable()
        movable = self.movable_rows()
        moving = [i for i in rows if i in movable]
        positions = {movable.index(i) for i in moving}
        can_up = editable and any(i > 0 and i - 1 not in positions for i in positions)
        can_down = editable and any(i + 1 < len(movable) and i + 1 not in positions for i in positions)

        def action(key, callback, enabled=True, shortcut=None):
            label_key = key
            count = len(rows)
            if key == 'remove':
                label_key = 'remove_one' if count == 1 else 'remove_count'
            elif key in {'move_up', 'move_down', 'move_top', 'move_bottom'} and len(moving) > 1:
                label_key += '_count'
                count = len(moving)
            item = menu.addAction(tr('files.' + label_key, count=count), callback)
            item.setData(key)
            item.setEnabled(enabled)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
            return item

        action('move_up', lambda: self.move_selected(-1), can_up, 'Alt+Up')
        action('move_down', lambda: self.move_selected(1), can_down, 'Alt+Down')
        action('move_top', lambda: self.move_rows(self.selected_rows(), 0), can_up)
        action('move_bottom', lambda: self.move_rows(self.selected_rows(), len(self.entries)),
               can_down)
        menu.addSeparator()
        action('sort_ascending', self.sort_queue, editable and len(movable) > 1)
        action('sort_descending', lambda: self.sort_queue(True), editable and len(movable) > 1)
        menu.addSeparator()
        action('open_folder', self.open_folder, bool(rows))
        menu.addSeparator()
        action('select_all', self.file_list.selectAll, bool(self.entries), 'Ctrl+A')
        paths = frozenset(str(self.entries[index].path) for index in rows)
        remove = action('remove', lambda: self.remove_selected(paths), editable and bool(rows), 'Delete')
        remove.setToolTip(tr('files.remove_hint'))
        remove.setProperty('destructive', True)
        return menu

    def queue_menu(self, position):
        item = self.file_list.itemAt(position)
        if item is not None:
            command = (QItemSelectionModel.SelectionFlag.NoUpdate if item.isSelected() or self.file_list.check_mode
                       else QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.file_list.setCurrentItem(item, command)
        menu = self.build_queue_menu()
        menu.exec(self.file_list.viewport().mapToGlobal(position))
        menu.deleteLater()

    def clear_queue(self):
        if not self.entries or not self.queue_editable():
            return
        paths = tuple(entry.path for entry in self.entries)
        confirmation = QMessageBox(
            QMessageBox.Icon.Question, tr('files.clear_queue_title'),
            tr('files.clear_queue_body', count=len(paths)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self,
        )
        confirmation.setTextFormat(Qt.TextFormat.PlainText)
        confirmation.button(QMessageBox.StandardButton.Yes).setText(tr('files.clear'))
        confirmation.button(QMessageBox.StandardButton.No).setText(tr('files.cancel_settings'))
        confirmation.setDefaultButton(QMessageBox.StandardButton.No)
        confirmation.setEscapeButton(QMessageBox.StandardButton.No)
        answer = confirmation.exec()
        confirmation.deleteLater()
        if (answer == QMessageBox.StandardButton.Yes and self.queue_editable()
                and paths == tuple(entry.path for entry in self.entries)):
            self.entries.clear()
            self.last_completed = None
            self.refresh_queue()
            self.update_status_visibility()
            self.metrics.setText(tr('files.metrics_empty'))
            self.audio_metrics.setText(tr('files.audio_metrics_empty'))
            self.percentage.setText(tr('files.no_progress'))
            self.progress.setValue(0)
            self.set_notice('ready')

    def open_folder(self):
        index = self.file_list.currentRow()
        if 0 <= index < len(self.entries):
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.entries[index].path.parent))):
                self.set_notice('folder_failed')

    def show_selected_detail(self):
        if not hasattr(self, 'detail'):
            return
        index = self.file_list.currentRow()
        if not 0 <= index < len(self.entries):
            self.detail.setText('')
            self.detail.setToolTip('')
            self.detail.hide()
            return
        entry = self.entries[index]
        if entry.failure:
            message = failure_text(entry.failure)
        elif entry.outputs:
            message = tr('files.outputs_saved', count=len(entry.outputs))
        else:
            message = ''
        self.detail.setText(message)
        self.detail.setToolTip('\n'.join(map(str, entry.outputs)) or message)
        self.detail.setVisible(bool(message))

    def edit_options(self):
        if self.busy:
            return
        dialog = FileOptionsDialog(self.options, self.endpoint, self)
        if dialog.exec():
            self.options = dialog.values
            self.set_notice('settings_applied')

    def on_progress(self, event):
        if self.current is None or self.stopping:
            return
        previous_processed = self.current.metrics.get('processed_seconds', 0)
        self.current.metrics.update(event)
        if event.get('type') == 'completed':
            self.current.metrics['eta_seconds'] = 0
            if 'speed' not in event:
                self.current.metrics.pop('speed', None)
        self.last_event_at = time.monotonic()
        stage = 'finishing' if event.get('type') == 'completed' else event.get('stage', 'starting')
        self.phase.setText(tr('files.stage.' + stage))
        if stage not in self.logged_stages:
            self.event_log.append(tr('files.stage.' + stage))
            self.logged_stages.add(stage)
        total = self.current.metrics.get('total_seconds')
        if total and not self.logged_duration:
            self.event_log.append(tr('files.log_duration', duration=duration(total)))
            self.logged_duration = True
        processed = self.current.metrics.get('processed_seconds', 0)
        if processed > previous_processed:
            if 'chunks_completed' not in event and 'chunk_seconds' not in self.current.metrics:
                self.current.metrics['chunks_completed'] = self.current.metrics.get('chunks_completed', 0) + 1
        if processed > self.last_logged_processed and event.get('type') != 'completed':
            snapshot = ProgressSnapshot.from_event(self.current.metrics)
            self.event_log.append(tr('files.log_progress',
                                     processed=duration(snapshot.processed_seconds),
                                     total=duration(snapshot.total_seconds),
                                     elapsed=duration(snapshot.elapsed_seconds),
                                     speed=f'{snapshot.speed:.2f}×'), 'progress')
            self.last_logged_processed = processed
        self.update_metrics()

    def on_outputs(self, paths):
        if self.current is not None:
            self.current.outputs = tuple(Path(path) for path in paths)

    def update_metrics(self):
        entry = self.current if self.busy else None
        if entry is None:
            return
        metrics = entry.metrics
        snapshot = ProgressSnapshot.from_event(metrics)
        if (self.busy and metrics.get('type') != 'completed' and self.last_event_at
                and metrics.get('stage') in {'transcribing', 'awaiting_result', 'saving'}):
            snapshot = snapshot.advance(max(0, time.monotonic() - self.last_event_at))
        percentage = snapshot.percentage
        if entry.state == 'completed':
            percentage = 100
        self.progress.setValue(round(percentage * 10) if percentage is not None else 0)
        chunks = metrics.get('chunks_completed', 0)
        done = metrics.get('stage') == 'saving' or metrics.get('type') == 'completed'
        segment = self.batch_options.get('file_seg_duration', 60)
        overlap = self.batch_options.get('file_seg_overlap', 4)
        total_chunks = None
        if done:
            total_chunks = max(1, chunks)
        elif snapshot.total_seconds:
            # Match the server threshold: a final tail can exceed one stride.
            threshold = segment + 2 * overlap
            full_chunks = max(0, math.floor((snapshot.total_seconds - threshold) / segment + 1e-9) + 1)
            total_chunks = full_chunks + int(snapshot.total_seconds - full_chunks * segment > 1e-8)
            total_chunks = max(total_chunks, chunks + 1)
        active = not done and metrics.get('stage') in {'transcribing', 'awaiting_result'}
        self.progress.set_chunks(chunks, total_chunks, active, segment_seconds=segment,
                                 audio_seconds=snapshot.processed_seconds if done else snapshot.total_seconds)
        chunk_label = tr('files.chunk_count', completed=chunks, total=total_chunks) if total_chunks else tr(
            'files.chunk_count_unknown', completed=chunks)
        self.percentage.setText(chunk_label)
        self.progress.setAccessibleDescription(chunk_label)
        first, last = self.progress.visible_chunks()
        self.progress.setToolTip(tr('files.chunk_legend', first=first + 1, last=last))
        last_speed = '—'
        chunk_detail = tr('files.chunk_waiting')
        if snapshot.chunk_elapsed_seconds > 0:
            last_speed = f'{snapshot.chunk_seconds / snapshot.chunk_elapsed_seconds:.2f}×'
            chunk_detail = tr('files.chunk_metrics', audio=duration(snapshot.chunk_seconds),
                              elapsed=f'{snapshot.chunk_elapsed_seconds:.2f}', speed=last_speed)
        self.chunk_metrics.setText(tr('files.speed_metrics',
                                      average=f'{snapshot.speed:.2f}×' if snapshot.speed else '—',
                                      last=last_speed))
        self.chunk_metrics.setToolTip(tr('files.metrics_help') + '\n' + chunk_detail)
        # Refresh clocks like the terminal; confirmed audio and bar width stay fixed.
        self.audio_metrics.setText(tr('files.audio_metrics', total=duration(snapshot.total_seconds),
                                       processed=duration(snapshot.processed_seconds),
                                       remaining=duration(snapshot.remaining_seconds)))
        self.metrics.setText(tr('files.live_metrics', elapsed=duration(snapshot.elapsed_seconds),
                                eta=duration(snapshot.eta_seconds) if snapshot.eta_seconds is not None else tr('file.calculating')))
        if self.current is not None:
            index = self.entries.index(self.current)
            if index < len(self.row_labels):
                self.row_labels[index].setText(tr('files.chunk_running', number=chunks + 1) if not done
                                               else tr('files.stage.finishing'))

    def start_batch(self):
        if self.busy or self.importer.active or self.closing:
            return
        try:
            self.batch_options = validate_options(self.options)
        except ValueError as exc:
            self.notice.setText(str(localize_notice(exc.args[0])))
            return
        self.batch_formats = frozenset(name for name, box in self.formats.items() if box.isChecked())
        self.batch = [entry for entry in self.entries if entry.state != 'completed']
        if not self.batch or not self.batch_formats:
            return
        if self.paused:
            self.event_log.append(tr('files.log_resumed'))
        self.paused = False
        self.pause_requested = False
        for entry in self.batch:
            entry.state = 'pending'
            entry.failure = ''
            entry.metrics.clear()
            entry.outputs = ()
        self.batch_entries = list(self.batch)
        self.batch_started_at = time.monotonic()
        self.event_log.append(tr('files.log_batch_start', count=len(self.batch_entries),
                                 formats=' / '.join(name.upper() for name in sorted(self.batch_formats))))
        self.event_log.append(tr('files.endpoint', endpoint=self.endpoint))
        self.event_log.append(tr('files.log_settings', language=self.batch_options['language'],
                                 segment=self.batch_options['file_seg_duration'], overlap=self.batch_options['file_seg_overlap']))
        self.busy = True
        self.stopping = False
        self.start_next()

    def start_next(self):
        # A confirmation can run a nested event loop while the file finishes.
        # Never start another file until that decision has been resolved.
        if self.confirming_abort and not self.closing:
            return
        if self.stopping or self.pause_requested or not self.batch:
            self.busy = False
            self.current = None
            self.paused = (not self.closing and (self.stopping or self.pause_requested)
                           and any(entry.state != 'completed' for entry in self.entries))
            self.pause_requested = False
            self.batch.clear()
            self.refresh_queue()
            self.set_notice('paused_discarded' if self.paused and self.stopping else
                            'paused_notice' if self.paused else 'stopped' if self.stopping else
                            'batch_failed' if any(entry.state == 'failed' for entry in self.entries) else
                            'pending_added' if any(entry.state == 'pending' for entry in self.entries) else 'finished')
            self.update_status_visibility()
            self.update_metrics()
            elapsed = max(0, time.monotonic() - self.batch_started_at) if self.batch_started_at else 0
            succeeded = sum(entry.state == 'completed' for entry in self.batch_entries)
            failed = sum(entry.state == 'failed' for entry in self.batch_entries)
            audio = sum(entry.metrics.get('processed_seconds', 0) for entry in self.batch_entries if entry.state == 'completed')
            self.event_log.append(tr('files.log_batch_end', total=len(self.batch_entries), succeeded=succeeded,
                                     failed=failed, pending=sum(entry.state in {'pending', 'cancelled'} for entry in self.batch_entries),
                                     audio=duration(audio), elapsed=duration(elapsed), speed=f'{audio / elapsed:.2f}×' if elapsed else '—'),
                                  'warning' if self.stopping or failed else 'success')
            if self.paused:
                self.event_log.append(tr('files.log_paused'))
            if self.closing:
                self.close()
            return
        self.current = self.batch.pop(0)
        self.current.state = 'running'
        self.started_at = time.monotonic()
        self.last_event_at = self.started_at
        self.logged_stages.clear()
        self.logged_duration = False
        self.last_logged_processed = 0.0
        self.phase.setText(tr('files.stage.starting'))
        self.active_name.value = self.current.path.name
        self.active_name.setToolTip(str(self.current.path))
        self.active_name.setText(self.active_name.fontMetrics().elidedText(
            self.current.path.name, Qt.TextElideMode.ElideMiddle, self.active_name.width()))
        self.current.metrics = {'processed_seconds': 0, 'total_seconds': None, 'stage': 'starting'}
        self.refresh_queue()
        self.set_notice('working', name=self.current.path.name)
        self.event_log.append(tr('files.log_file_start', index=self.batch_entries.index(self.current) + 1,
                                 total=len(self.batch_entries), name=self.current.path.name))
        self.event_log.append(tr('files.log_source', path=str(self.current.path)))
        self.update_metrics()
        if not self.runner.start(self.current.path, self.batch_formats, self.batch_options):
            self.file_finished(False, 'start_failed')

    def file_finished(self, success, reason):
        if self.current is None:
            return
        self.current.state = 'completed' if success else 'cancelled' if self.stopping or reason == 'cancelled' else 'failed'
        if not success and self.current.state != 'cancelled':
            self.current.failure = reason
            if reason in {'connection_failed', 'decoder_unavailable', 'config_invalid', 'start_failed', 'startup_timeout'}:
                self.batch.clear()
        if self.current.metrics.get('type') != 'completed':
            snapshot = ProgressSnapshot.from_event(self.current.metrics)
            delta = max(0, time.monotonic() - self.last_event_at) if self.last_event_at else 0
            elapsed = snapshot.advance(delta).elapsed_seconds
            self.current.metrics['elapsed_seconds'] = elapsed
        snapshot = ProgressSnapshot.from_event(self.current.metrics)
        if success:
            self.event_log.append(tr('files.log_file_done', name=self.current.path.name,
                                     audio=duration(snapshot.processed_seconds), elapsed=duration(snapshot.elapsed_seconds),
                                     speed=f'{snapshot.speed:.2f}×', rtf=f'{snapshot.rtf:.3f}' if snapshot.rtf is not None else '—'), 'success')
            if self.current.metrics.get('sequence', 1) > 1:
                self.event_log.append(tr('files.log_numbered', sequence=self.current.metrics['sequence']), 'warning')
            if 'text_length' in self.current.metrics:
                self.event_log.append(tr('files.log_text_length', count=self.current.metrics['text_length']))
            for path in self.current.outputs:
                self.event_log.append(tr('files.log_output', path=str(path)), 'output')
        elif self.current.state == 'cancelled':
            self.event_log.append(tr('files.log_file_cancelled', name=self.current.path.name,
                                     elapsed=duration(max(0, time.monotonic() - self.started_at))), 'warning')
            self.current.metrics.clear()
            self.current.outputs = ()
        else:
            self.event_log.append(tr('files.log_file_failed', name=self.current.path.name, reason=failure_text(reason)), 'error')
        self.last_completed = self.current
        self.current = None
        self.start_next()

    def toggle_pause(self):
        if not self.busy or self.stopping or self.confirming_abort or self.closing:
            return
        self.pause_requested = not self.pause_requested
        key = 'pause_requested' if self.pause_requested else 'pause_revoked'
        self.set_notice(key)
        self.event_log.append(tr('files.' + key))
        self.update_controls()

    def confirm_stop(self):
        if not self.busy or self.current is None or self.stopping or self.confirming_abort or self.closing:
            return
        current = self.current
        confirmation = QMessageBox(
            QMessageBox.Icon.Warning, tr('files.abort_title'),
            tr('files.abort_body', name=current.path.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self,
        )
        confirmation.setTextFormat(Qt.TextFormat.PlainText)
        confirmation.button(QMessageBox.StandardButton.Yes).setText(tr('files.abort_confirm'))
        confirmation.button(QMessageBox.StandardButton.Yes).setObjectName('danger')
        confirm_button = confirmation.button(QMessageBox.StandardButton.Yes)
        confirm_button.style().unpolish(confirm_button)
        confirm_button.style().polish(confirm_button)
        confirmation.button(QMessageBox.StandardButton.No).setText(tr('files.keep_processing'))
        confirmation.setDefaultButton(QMessageBox.StandardButton.No)
        confirmation.setEscapeButton(QMessageBox.StandardButton.No)
        self.confirming_abort = True
        self.update_controls()
        try:
            answer = confirmation.exec()
        finally:
            self.confirming_abort = False
            confirmation.deleteLater()
        if answer == QMessageBox.StandardButton.Yes and not self.closing:
            self.pause_requested = True
            if self.current is current and current.metrics.get('type') != 'completed':
                self.stop_batch()
            else:
                self.set_notice('pause_requested')
        if self.current is None and self.busy:
            self.start_next()
        self.update_controls()

    def stop_batch(self):
        if self.busy and not self.stopping:
            self.stopping = True
            self.set_notice('stopping')
            self.phase.setText(tr('files.stopping'))
            self.event_log.append(tr('files.log_cancel_requested'), 'warning')
            self.update_controls()
            self.runner.cancel()

    def closeEvent(self, event):
        if self.busy or self.importer.active:
            self.closing = True
            self.importer.cancel()
            self.stop_batch()
            self.setEnabled(False)
            event.ignore()
        else:
            self.clock.stop()
            event.accept()
