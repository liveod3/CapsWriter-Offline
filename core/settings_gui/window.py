"""Qt settings window. All widgets live on the process main thread."""

import json
from queue import Empty, Queue
import threading
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from core.i18n import get_language, system_language, tr
from .backend import safe_error
from .bridge import Disconnected, RemoteError
from .choices import ConfigChoice, MicrophoneChoice, RecognitionChoice
from .device_watch import DeviceWatch
from .fields import PAGES, page_index
from .help_widgets import DeviceNotice, SettingsGroups, field_caption
from .history_page import HistoryPage
from .recent_words import RecentWords
from .status_page import StatusPage
from .presentation import GROUP_STARTS, Choice, DecimalInput, HomePage, IntegerInput, Toggle, apply_theme, text
from .shell import show_window
from .validation import field_errors


def label(key):
    return tr('gui.' + key)


def make_input(name, kind):
    if name == 'language':
        widget = RecognitionChoice()
    elif name == 'input_device':
        widget = MicrophoneChoice()
    elif kind is bool:
        widget = Toggle()
        widget.setFixedSize(widget.sizeHint())
    elif isinstance(kind, tuple):
        widget = Choice()
        for choice in kind:
            widget.addItem(label('choice.' + choice) if choice in ('minimal', 'natural', 'fluent', 'custom', 'correction',
                                                                 'auto', 'zh-CN', 'en', 'openai', 'ollama')
                           else choice, choice)
    elif kind in (int, float):
        widget = IntegerInput() if kind is int else DecimalInput()
        widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        widget.setRange(0, 1000000)
        if kind is float:
            widget.setDecimals(6)
    else:
        widget = QLineEdit()
    widget.setObjectName(name)
    widget.setAccessibleName(label(name))
    if kind in (int, float):
        widget.setMaximumWidth(160)
        widget.setKeyboardTracking(False)
    elif isinstance(kind, tuple):
        widget.setMaximumWidth(260)
    elif name == 'port':
        widget.setMaximumWidth(160)
    return widget


def get_value(widget, kind):
    if kind is bool:
        return widget.isChecked()
    if isinstance(widget, QComboBox):
        return widget.currentData()
    if kind in (int, float):
        return widget.value()
    value = widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()
    return value or None if kind is None else value


def set_value(widget, kind, value):
    if kind is bool:
        widget.setChecked(bool(value))
    elif isinstance(widget, ConfigChoice):
        widget.set_config_value(value)
    elif isinstance(widget, QComboBox):
        index = widget.findData(value)
        if index < 0:
            widget.addItem(str(value), value)
            index = widget.count() - 1
        widget.setCurrentIndex(index)
    elif kind in (int, float):
        widget.setValue(value)
    elif isinstance(widget, QPlainTextEdit):
        widget.setPlainText(value or '')
    else:
        widget.setText('' if value is None else str(value))


class SettingsWindow(QMainWindow):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.snapshot = None
        self.form_baseline = {}
        self.startup_language = get_language()
        self.save_error = None
        self.input_errors = {}
        self.confirming_exit = False
        self.discard_on_exit = False
        self.close_pending = False
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.timeout.connect(self.save)
        self.devices_loaded_once = False
        self.devices_inflight = False
        self.devices_pending = False
        self.device_watch = DeviceWatch(self, self.devices_visible, self.refresh_devices)
        self.device_retry = QTimer(self)
        self.device_retry.setSingleShot(True)
        self.device_retry.timeout.connect(self.refresh_devices)
        self.catalog = None
        self.fields = {}
        self.field_states = {}
        self.llm_controls = []
        self.editors = {}
        self.editor_original = {}
        self.editor_ids = {}
        self.loading = False
        self.busy = False
        self.quiet = False
        self.pending_request = None
        self.next_settings_poll = 0
        self.latest_state = None
        self.catalog_conflict = False
        self.config_conflict = False
        self.icon_recording = None
        self.desktop_mode = False
        self.tray = None
        self.exit_pending = False
        self.exiting = False
        self.last_attention = None
        self.closed = threading.Event()
        self.requests = Queue(maxsize=1)
        self.responses = Queue(maxsize=1)
        self.setWindowTitle('CapsWriter')
        self.resize(1120, 860)
        self.setMinimumSize(800, 600)
        if self.screen():
            available = self.screen().availableGeometry()
            self.resize(min(1120, max(800, available.width() - 80)),
                        min(860, max(600, available.height() - 80)))
        apply_theme(self)
        central = QWidget()
        central.setObjectName('workspace')
        self.setCentralWidget(central)
        body = QHBoxLayout(central)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(218)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 30, 18, 22)
        side.setSpacing(12)
        brand = QLabel('CapsWriter')
        brand.setObjectName('brand')
        side.addWidget(brand)
        side.addWidget(text('workspace', 'muted'))
        side.addSpacing(18)
        self.home_button = QPushButton(label('home'))
        self.home_button.setObjectName('home')
        self.home_button.clicked.connect(self.show_home)
        side.addWidget(self.home_button)
        self.history_button = QPushButton(label('history'))
        self.history_button.setObjectName('home')
        self.history_button.clicked.connect(self.show_history)
        side.addWidget(self.history_button)
        side.addSpacing(16)
        side.addWidget(text('title', 'eyebrow'))
        self.navigation = QListWidget()
        self.navigation.setAccessibleName(label('pages'))
        side.addWidget(self.navigation, 1)
        self.exit_button = QPushButton(label('exit_client'))
        self.exit_button.setObjectName('exit')
        self.exit_button.clicked.connect(self.request_exit)
        self.exit_button.hide()
        side.addWidget(self.exit_button)
        body.addWidget(sidebar)
        self.workspace = QStackedWidget()
        body.addWidget(self.workspace, 1)
        self.home = HomePage(self.navigate, self.home_action, self.start_desktop, RecentWords(self.request))
        home_scroll = QScrollArea()
        home_scroll.setWidgetResizable(True)
        home_scroll.setWidget(self.home)
        self.workspace.addWidget(home_scroll)
        settings = QWidget()
        layout = QVBoxLayout(settings)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.workspace.addWidget(settings)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        for page, fields in PAGES.items():
            self.navigation.addItem(label('page.' + page))
            content = QWidget()
            content.setObjectName('workspace')
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(30, 26, 30, 26)
            content_layout.setSpacing(14)
            heading = text('page.' + page, 'heading')
            content_layout.addWidget(heading)
            description = text('intro.' + page, 'description')
            content_layout.addWidget(description)
            groups = SettingsGroups()
            content_layout.addWidget(groups)
            for name, kind in fields.items():
                if name in GROUP_STARTS:
                    group = groups.add_group(GROUP_STARTS[name])
                    if page == 'text' and name != 'llm_enabled':
                        self.llm_controls.append(groups.cards[-1])
                    form = QFormLayout()
                    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                    form.setHorizontalSpacing(20)
                    form.setVerticalSpacing(6)
                    group.addLayout(form)
                if form.rowCount():
                    divider = QFrame()
                    divider.setObjectName('settingDivider')
                    divider.setFixedHeight(1)
                    form.addRow(divider)
                widget = make_input(name, kind)
                self.fields[name] = (widget, kind)
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(12)
                row_layout.addWidget(widget, 0 if kind is bool else 1)
                state = QLabel()
                state.setObjectName('state')
                state.setWordWrap(True)
                self.field_states[name] = state
                caption = field_caption(name, widget)
                row_layout.insertWidget(0, caption)
                if (kind in (bool, int, float) or isinstance(widget, QComboBox) or name == 'port') \
                        and name != 'input_device':
                    row_layout.setStretch(1, 0)
                    row_layout.insertStretch(1, 1)
                field = QWidget()
                field_layout = QVBoxLayout(field)
                field_layout.setContentsMargins(0, 0, 0, 0)
                field_layout.setSpacing(5)
                field_layout.addWidget(row)
                if name == 'input_device':
                    self.device_refresh = QPushButton(label('refresh_devices'))
                    self.device_refresh.setAccessibleName(label('refresh_devices_help'))
                    self.device_refresh.setToolTip(label('refresh_devices_help'))
                    self.device_refresh.clicked.connect(self.refresh_devices)
                    row_layout.addWidget(self.device_refresh)
                    self.device_notice = DeviceNotice()
                    row_layout.addWidget(self.device_notice)
                field_layout.addWidget(state, 0, Qt.AlignmentFlag.AlignLeft)
                form.addRow(field)
                if name in ('save_llm_records', 'save_llm_context', 'llm_cost_tracking', 'diagnostic_include_context'):
                    self.llm_controls.append(field)
                if name == 'llm_enabled':
                    self.add_provider_selector(group)
                    self.cleanup_notice = text('cleanup_advanced', 'muted')
                    self.cleanup_notice.hide()
                    group.addWidget(self.cleanup_notice)
            if page == 'status':
                self.dashboard = StatusPage(self.request)
                content_layout.addWidget(self.dashboard)
            if page == 'diagnostics':
                tools_layout = groups.add_group('diagnostic_tools')
                self.runtime = text('standalone', 'muted')
                tools_layout.addWidget(self.runtime)
                self.runtime_buttons = []
                for action in ('toggle_pause', 'reconnect_microphone'):
                    divider = QFrame()
                    divider.setObjectName('settingDivider')
                    divider.setFixedHeight(1)
                    tools_layout.addWidget(divider)
                    row = QVBoxLayout()
                    row.setSpacing(8)
                    button = QPushButton(label(action))
                    button.setEnabled(False)
                    button.clicked.connect(lambda _checked=False, action=action: self.request(
                        'action', {'name': action}, lambda result: self.status.setText(result or label('action_sent'))))
                    self.runtime_buttons.append(button)
                    row.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
                    row.addWidget(text('debug_' + action, 'muted'))
                    tools_layout.addLayout(row)
                report_frame = QFrame()
                report_frame.setObjectName('card')
                report_layout = QVBoxLayout(report_frame)
                report_layout.setContentsMargins(18, 16, 18, 16)
                report_layout.setSpacing(10)
                report_layout.addWidget(text('diagnostic_report', 'cardTitle'))
                report_layout.addWidget(text('diagnostic_report_hint', 'muted'))
                actions = QHBoxLayout()
                diagnostic = QPushButton(label('recent'))
                diagnostic.clicked.connect(lambda: self.request('diagnostics', {}, self.show_report))
                actions.addWidget(diagnostic)
                self.copy_report = QPushButton(label('diagnostic_copy'))
                self.copy_report.setEnabled(False)
                self.copy_report.clicked.connect(lambda: QApplication.clipboard().setText(self.report.toPlainText()))
                actions.addWidget(self.copy_report)
                actions.addStretch()
                report_layout.addLayout(actions)
                self.report = QPlainTextEdit()
                self.report.setReadOnly(True)
                self.report.setAccessibleName(label('report'))
                self.report.setPlaceholderText(label('diagnostic_report_empty'))
                self.report.setMinimumHeight(260)
                report_layout.addWidget(self.report)
                content_layout.addWidget(report_frame)
            content_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            groups.bind_viewport(scroll.viewport(), 60)
            self.pages.addWidget(scroll)
        self.navigation.addItem(label('page.advanced'))
        advanced_page = QWidget()
        advanced_page.setObjectName('workspace')
        advanced_layout = QVBoxLayout(advanced_page)
        advanced_layout.setContentsMargins(30, 26, 30, 26)
        advanced_layout.setSpacing(16)
        advanced_layout.addWidget(text('page.advanced', 'heading'))
        advanced_layout.addWidget(text('advanced_intro', 'description'))
        advanced_groups = SettingsGroups(minimum_column_width=350, equal_height=True)
        advanced_layout.addWidget(advanced_groups)
        client_box = advanced_groups.add_group('advanced_client_title')
        client_box.addWidget(text('advanced_client_summary'))
        client_box.addStretch()
        self.advanced = QPushButton(label('advanced'))
        self.advanced.setToolTip(label('advanced_help'))
        self.advanced.clicked.connect(self.open_advanced)
        client_box.addSpacing(6)
        client_box.addWidget(self.advanced, 0, Qt.AlignmentFlag.AlignLeft)
        presets_box = advanced_groups.add_group('advanced_presets_title')
        presets_box.addWidget(text('advanced_presets_summary'))
        presets_box.addStretch()
        presets_file = QPushButton(label('advanced_presets'))
        presets_file.clicked.connect(lambda: self.request('advanced', {'file': 'presets'}, lambda _: None))
        presets_box.addSpacing(6)
        presets_box.addWidget(presets_file, 0, Qt.AlignmentFlag.AlignLeft)
        note = QFrame()
        note.setObjectName('providerDetails')
        note_box = QVBoxLayout(note)
        note_box.setContentsMargins(18, 14, 18, 14)
        note_box.setSpacing(6)
        note_box.addWidget(text('advanced_apply_title', 'cardTitle'))
        note_box.addWidget(text('advanced_apply_summary'))
        advanced_layout.addWidget(note)
        advanced_layout.addStretch()
        advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setWidget(advanced_page)
        advanced_groups.bind_viewport(advanced_scroll.viewport(), 60)
        self.pages.addWidget(advanced_scroll)
        self.navigation.currentRowChanged.connect(self.navigate)
        self.navigation.itemClicked.connect(lambda _: self.navigate(self.navigation.currentRow()))
        footer_widget = QWidget()
        footer_widget.setObjectName('footer')
        footer_layout = QVBoxLayout(footer_widget)
        footer_layout.setContentsMargins(30, 12, 30, 16)
        self.status = QLabel(label('loading'))
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        footer_layout.addWidget(self.status)
        self.save_status = text('autosave_help', 'muted')
        footer_layout.addWidget(self.save_status)
        self.retry_save = QPushButton(label('retry_save'))
        self.retry_save.clicked.connect(self.retry_autosave)
        self.retry_save.hide()
        footer_layout.addWidget(self.retry_save, 0, Qt.AlignmentFlag.AlignLeft)
        self.resolve_button = QPushButton(label('use_file_version'))
        self.resolve_button.clicked.connect(self.reload)
        self.resolve_button.hide()
        footer_layout.addWidget(self.resolve_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(footer_widget)
        self.history = HistoryPage(self.request)
        self.workspace.addWidget(self.history)
        for button in self.findChildren(QPushButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_home()
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save)
        for widget, kind in self.fields.values():
            signal = (widget.toggled if kind is bool else widget.currentIndexChanged
                      if isinstance(widget, QComboBox) else widget.valueChanged
                      if kind in (int, float) else widget.textChanged)
            signal.connect(self.settings_edited)
        self.fields['llm_enabled'][0].toggled.connect(self.update_llm_controls)
        self.update_llm_controls()
        self.thread = threading.Thread(target=self.work, daemon=True, name='settings-worker')
        self.thread.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.deliver)
        self.timer.start(50)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.poll_state)
        self.poll.start(200)
        self.request('read', {}, self.loaded)

    def add_provider_selector(self, layout):
        panel = QWidget()
        self.llm_controls.append(panel)
        layout.addWidget(panel)
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        form = QFormLayout()
        form.setVerticalSpacing(6)
        box.addLayout(form)
        widgets = {}
        for name in ('provider',):
            widget = Choice()
            widget.setMaximumWidth(330)
            widget.setMinimumContentsLength(12)
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            widget.setAccessibleName(label(name))
            widget.setToolTip(label('help.' + name))
            widget.setAccessibleDescription(label('help.' + name))
            divider = QFrame()
            divider.setObjectName('settingDivider')
            divider.setFixedHeight(1)
            form.addRow(divider)
            row = QHBoxLayout()
            row.setSpacing(20)
            row.addWidget(field_caption(name, widget))
            row.addStretch()
            row.addWidget(widget, 1)
            form.addRow(row)
            widgets[name] = (widget, str)
        details = QFrame()
        details.setObjectName('providerDetails')
        details_box = QVBoxLayout(details)
        details_box.setContentsMargins(14, 12, 14, 12)
        details_box.setSpacing(8)
        details_box.addWidget(text('provider_details', 'muted'))
        info = QFormLayout()
        info.setHorizontalSpacing(20)
        info.setVerticalSpacing(7)
        info.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        details_box.addLayout(info)
        self.provider_info = {}
        for key in ('model', 'kind', 'price_input', 'price_output', 'price_cached',
                    'price_cache_write', 'price_reasoning', 'price_updated'):
            value = QLabel()
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            value.setMinimumWidth(0)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setAccessibleName(label(key))
            info.addRow(text(key, 'muted'), value)
            self.provider_info[key] = value
        self.provider_info_form = info
        self.price_note = text('price_note', 'muted')
        details_box.addWidget(self.price_note)
        box.addWidget(details)
        self.provider_notice = text('providers_empty', 'muted')
        self.provider_notice.hide()
        box.addWidget(self.provider_notice)
        self.provider_status = text('autosaving', 'muted')
        self.provider_status.hide()
        box.addWidget(self.provider_status)
        self.editors['presets'] = (None, None, widgets, None)
        widgets['provider'][0].currentIndexChanged.connect(self.update_provider_details)
        # Only user activation writes; loading and metadata refresh remain read-only.
        widgets['provider'][0].activated.connect(self.provider_selected)

    def provider_selected(self, *_):
        self.provider_status.hide()
        self.save_entry('presets')

    def update_llm_controls(self, *_):
        enabled = self.fields['llm_enabled'][0].isChecked()
        for widget in self.llm_controls:
            widget.setEnabled(enabled)

    def entry_values(self, kind):
        widgets = self.editors[kind][2]
        return 'correct_asr', {name: get_value(widget, spec) for name, (widget, spec) in widgets.items()}

    def entry_dirty(self, kind):
        return kind in self.editor_original and self.entry_values(kind) != self.editor_original[kind]

    def entry_changes(self, kind):
        identifier, values = self.entry_values(kind)
        if kind == 'presets' and identifier not in self.catalog['presets']:
            return identifier, {**values, 'name': 'Text cleanup', 'prompt_mode': 'correction',
                                'use_caret_context': True}
        if not self.editor_ids.get(kind):
            return identifier, values
        original = self.editor_original[kind][1]
        return identifier, {key: value for key, value in values.items() if value != original.get(key)}

    def confirm_discard(self):
        return QMessageBox.question(self, label('unsaved'), label('discard'),
                                    QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Discard

    def populate_entry(self, kind, identifier):
        widgets = self.editors[kind][2]
        self.editor_ids[kind] = identifier
        defaults = {'provider': ''}
        defaults.update(self.catalog[kind].get(identifier, {}))
        for name, (widget, spec) in widgets.items():
            set_value(widget, spec, defaults.get(name, ''))
        self.editor_original[kind] = self.entry_values(kind)
        self.update_provider_details()
        self.update_cleanup_notice()

    def save_entry(self, kind):
        if self.blocked or not self.catalog or not self.fields['llm_enabled'][0].isChecked():
            return
        if not self.entry_dirty(kind):
            return
        identifier, changes = self.entry_changes(kind)
        provider_id = self.entry_values(kind)[1]['provider']
        provider = self.catalog['providers'].get(provider_id)
        if not provider or not provider.get('credentials_ready', True):
            return
        # Serialize the transaction with existing foreground actions. Controls stay
        # disabled until it finishes, so a later choice cannot overtake this write.
        if self.request('catalog_save', {'kind': kind, 'identifier': identifier, 'changes': changes,
                        'revision': self.catalog['revision']},
                        lambda result: self.catalog_saved(result, kind, identifier)):
            self.provider_status.setText(label('autosaving'))
            self.provider_status.show()

    def catalog_saved(self, result, kind, identifier):
        # Advance the revision after our own successful transaction; the backend
        # rejects external writes before a stale selection can overwrite them.
        self.catalog = result
        self.update_choices()
        self.fill_selector(kind, identifier)
        self.provider_status.hide()
        self.status.setText(label('catalog_saved'))

    def fill_selector(self, kind, identifier=None):
        self.populate_entry(kind, 'correct_asr')

    def catalog_loaded(self, result):
        self.catalog = result
        self.catalog_conflict = False
        self.provider_status.hide()
        self.update_choices()
        for kind in self.editors:
            self.fill_selector(kind, self.editor_ids.get(kind))
        self.update_conflict()

    def update_choices(self):
        widget = self.editors['presets'][2]['provider'][0]
        current = widget.currentData()
        previous = widget.blockSignals(True)
        widget.clear()
        widget.addItem(label('choose_provider'), '')
        widget.model().item(0).setEnabled(False)
        for entry, provider in self.catalog['providers'].items():
            ready = provider.get('credentials_ready', True)
            title = entry if ready else tr('gui.provider_unavailable', provider=entry)
            widget.addItem(title, entry)
            widget.model().item(widget.count() - 1).setEnabled(ready)
        if current is not None:
            set_value(widget, str, current)
        widget.blockSignals(previous)
        self.update_provider_details()

    def update_provider_details(self):
        if not self.catalog:
            return
        widgets = self.editors['presets'][2]
        provider = self.catalog['providers'].get(widgets['provider'][0].currentData(), {})
        ready = bool(provider) and provider.get('credentials_ready', True)
        available = any(item.get('credentials_ready', True) for item in self.catalog['providers'].values())
        self.provider_notice.setText(label('providers_empty' if not available else 'provider_needs_key'))
        self.provider_notice.setVisible(not ready)
        self.provider_info['model'].setText(provider.get('model') or label('unknown'))
        self.provider_info['kind'].setText(label('choice.' + provider['kind']) if provider else label('unknown'))
        rate = provider.get('pricing') or {}
        for field, key in (('price_input', 'input'), ('price_output', 'output'), ('price_cached', 'cached_input'),
                           ('price_cache_write', 'cache_write'), ('price_reasoning', 'reasoning')):
            value = self.provider_info[field]
            value.setText(tr('gui.price_amount', amount=rate[key], currency=rate['currency'])
                          if key in rate else label('price_unknown'))
            self.provider_info_form.setRowVisible(value, key in ('input', 'output') or key in rate)
        self.provider_info['price_updated'].setText(rate.get('updated') or label('unknown'))
        status = provider.get('pricing_status', 'missing')
        self.price_note.setText(label('price_' + status))

    def update_cleanup_notice(self):
        if not self.catalog or not self.snapshot or not self.snapshot.get('saved'):
            return
        config = self.snapshot['saved']['ClientConfig']
        preset = self.catalog['presets'].get('correct_asr')
        custom = (not preset or preset['prompt_mode'] != 'correction'
                  or config.get('llm_default_preset') != 'correct_asr'
                  or not config.get('llm_correction_enabled', True))
        self.cleanup_notice.setVisible(custom)

    def changes(self):
        if not self.snapshot or not self.snapshot.get('saved'):
            return {}
        result = {}
        for name, (widget, spec) in self.fields.items():
            value = get_value(widget, spec)
            # Compare the displayed draft to its initial display. Unedited numeric
            # types, empty nullable strings and precision must survive a save.
            if value != self.form_baseline.get(name):
                result[name] = value
        return result

    def loaded(self, result):
        self.populate_settings(result)
        self.update_state(result)
        self.request('catalog', {}, self.catalog_loaded)

    def populate_settings(self, result):
        self.snapshot = result
        if result.get('saved'):
            for name, (widget, spec) in self.fields.items():
                previous = widget.blockSignals(True)
                set_value(widget, spec, result['saved']['ClientConfig'].get(name))
                widget.blockSignals(previous)
            self.capture_baseline()
            self.update_device_notice()
            self.save_error = None
            self.input_errors = {}
            self.autosave.stop()
            self.update_save_status()
            self.update_cleanup_notice()
            self.update_llm_controls()

    def update_state(self, result):
        self.latest_state = result
        self.update_runtime(result.get('runtime'))
        if result.get('activate'):
            show_window(self)
        if result.get('error'):
            self.status.setText(result['error'])
            return
        self.config_conflict = bool(self.snapshot and result['revision'] != self.snapshot['revision'])
        changes = self.changes()
        for name, state in self.field_states.items():
            qualified = 'ClientConfig.' + name
            if name in changes:
                message = 'unsaved'
            elif name == 'ui_language' and self.language_restart():
                message = 'restart'
            elif qualified in (result.get('restart_required') or []):
                message = 'restart'
            elif qualified in (result.get('pending') or []):
                message = 'pending'
            else:
                message = 'effective' if result.get('effective') else 'saved'
            state.setText(self.input_errors.get(name, label(message)))
            state.setVisible(name in self.input_errors or message not in ('saved', 'effective'))
            changed = name in self.input_errors or message in ('unsaved', 'restart', 'pending')
            if state.property('changed') != changed:
                state.setProperty('changed', changed)
                state.style().unpolish(state)
                state.style().polish(state)
            effective = (result.get('effective') or {}).get('ClientConfig', {}).get(name)
            state.setToolTip(label('effective_value') + str(effective))
            self.fields[name][0].setToolTip(label(message) + '\n' + label('effective_value') + str(effective))
        self.status.setText(label('attached' if result.get('effective') else 'standalone'))
        self.update_conflict()

    def update_runtime(self, runtime):
        if self.latest_state is not None:
            self.latest_state['runtime'] = runtime
        result = {**(self.latest_state or {}), 'runtime': runtime}
        self.home.update_snapshot(result)
        desktop = result.get('desktop') or {}
        attention = result.get('error') or (desktop.get('message') if desktop.get('phase') == 'failed' else None)
        if self.desktop_mode and attention and attention != self.last_attention:
            show_window(self)
        self.last_attention = attention
        if self.desktop_mode:
            self.home.note.setText(label('home_running' if self.tray else 'home_without_tray'))
        if self.tray:
            self.tray.setToolTip('CapsWriter · ' + self.home.headline.text())
        recording = bool(runtime and runtime.get('recording'))
        if recording != self.icon_recording:
            self.icon_recording = recording
            icon = self.home.mark.icons[recording]
            self.setWindowIcon(icon)
            if self.tray:
                self.tray.setIcon(icon)
        if runtime:
            self.runtime.setText(' · '.join(label(key) + ': ' + label('yes' if runtime.get(key) else 'no')
                                           for key in ('connected', 'recording', 'paused', 'file_active')))
        else:
            self.runtime.setText(label('standalone'))
        for button in self.runtime_buttons:
            button.setEnabled(bool(runtime))

    def update_conflict(self):
        conflict = self.config_conflict or self.catalog_conflict
        self.resolve_button.setVisible(conflict)
        if conflict:
            self.status.setText(label('conflict'))

    def polled(self, result):
        if (not result.get('error') and self.snapshot and result['revision'] != self.snapshot['revision']
                and not self.changes() and self.pending_request is None):
            self.populate_settings(result)
        self.update_state(result)
        if self.pending_request is None:
            self.request('catalog', {}, self.catalog_polled, quiet=True)

    def catalog_polled(self, result):
        if not self.catalog or result['revision'] != self.catalog['revision']:
            if any(self.entry_dirty(kind) for kind in self.editors) or self.pending_request is not None:
                self.catalog_conflict = True
                self.update_conflict()
            else:
                self.catalog_loaded(result)
        elif result['providers'] != self.catalog['providers']:
            # Price/credential metadata can change without changing routing files.
            # Refresh details while retaining the user's pending provider selection.
            self.catalog['providers'] = result['providers']
            self.update_choices()

    def poll_state(self):
        if self.busy or self.exiting:
            return
        if time.monotonic() >= self.next_settings_poll:
            self.next_settings_poll = time.monotonic() + 2
            self.request('read', {}, self.polled, quiet=True)
        elif self.desktop_mode:
            self.request('status', {}, self.update_runtime, quiet=True)

    def navigate(self, page):
        if isinstance(page, str):
            page = page_index(page)
        if page < 0:
            return
        self.pages.setCurrentIndex(page)
        self.workspace.setCurrentIndex(1)
        blocked = self.navigation.blockSignals(True)
        self.navigation.setCurrentRow(page)
        self.navigation.blockSignals(blocked)
        self.update_navigation()
        if page == page_index('dictation'):
            self.device_watch.start()
            self.refresh_devices()

    def devices_visible(self):
        return (self.isVisible() and self.workspace.currentIndex() == 1 and self.pages.currentIndex() == page_index('dictation')
                and not self.closed.is_set() and not self.exiting and not self.exit_pending)

    def refresh_devices(self):
        if self.closed.is_set() or self.exiting or self.exit_pending:
            return
        if self.devices_inflight:
            self.devices_pending = True
            return
        if self.busy or self.fields['input_device'][0].view().isVisible():
            self.device_retry.start(100)
            return
        self.device_retry.stop()
        self.devices_inflight = True
        self.update_device_notice()
        self.request('input_devices', {}, self.devices_loaded, quiet=True)

    def devices_loaded(self, result):
        self.devices_inflight = False
        self.devices_loaded_once = True
        widget = self.fields['input_device'][0]
        if widget.view().isVisible():
            # A device can change while the menu is open; never move a click target.
            self.devices_pending = True
        else:
            widget.replace_inventory(result)
        self.update_device_notice()
        if self.devices_pending:
            self.devices_pending = False
            self.device_retry.start(500)

    def update_device_notice(self):
        widget = self.fields['input_device'][0]
        result = widget.inventory
        if result is None:
            message = None
        elif result.get('error'):
            message = 'devices_timeout' if result['error'] == 'timeout' else 'devices_unavailable'
        elif not result['devices']:
            message = 'devices_empty'
        elif not widget.selection_found():
            message = 'device_not_found'
        elif result.get('partial'):
            message = 'devices_partial'
        else:
            message = None
        self.device_notice.set_notice(label(message) if message else None)

    def update_navigation(self):
        for button, page in ((self.home_button, 0), (self.history_button, 2)):
            button.setProperty('active', self.workspace.currentIndex() == page)
            button.style().unpolish(button)
            button.style().polish(button)

    def show_home(self):
        self.workspace.setCurrentIndex(0)
        self.navigation.setCurrentRow(-1)
        self.update_navigation()

    def show_history(self):
        self.workspace.setCurrentIndex(2)
        self.navigation.setCurrentRow(-1)
        self.update_navigation()
        if not self.history.initialized:
            self.history.search_history()

    def home_action(self, name):
        def completed(result):
            if result:
                self.home.detail.setText(result)
                self.status.setText(result)
            else:
                self.poll_state()
        self.request('action', {'name': name}, completed)

    def start_desktop(self):
        self.request('desktop_start', {}, lambda _: self.poll_state())

    def reload(self):
        if self.blocked:
            return
        if (self.changes() or any(self.entry_dirty(kind) for kind in self.editors)) and not self.confirm_discard():
            return
        self.request('read', {}, self.loaded)

    def save(self):
        self.autosave.stop()
        if self.closed.is_set() or self.exiting or not self.snapshot or not self.snapshot.get('saved'):
            return
        if self.save_error or self.config_conflict:
            return
        changes = self.changes()
        self.input_errors = field_errors({**self.snapshot['saved']['ClientConfig'], **changes})
        if not changes or self.input_errors:
            if self.input_errors:
                self.close_pending = False
            self.update_save_status()
            return
        if self.busy:
            self.autosave.start(100)
            return
        submitted = {name: get_value(widget, spec) for name, (widget, spec) in self.fields.items()}
        self.save_status.setText(label('autosaving'))
        self.request('save', {'changes': changes, 'revision': self.snapshot['revision']},
                     lambda result: self.saved(result, submitted), quiet=True)

    def saved(self, result, submitted):
        result = {**{key: (self.latest_state or {}).get(key) for key in ('runtime', 'desktop')}, **result}
        self.snapshot = result
        # Only acknowledge the submitted draft. Edits made during disk I/O remain dirty.
        self.form_baseline = submitted
        self.save_error = None
        self.update_state(result)
        self.update_save_status()
        if self.changes():
            self.autosave.start(100)

    def settings_edited(self, *_):
        if not self.snapshot or not self.snapshot.get('saved') or self.loading or self.closed.is_set() or self.exiting:
            return
        self.save_error = None
        self.update_device_notice()
        self.input_errors = field_errors({**self.snapshot['saved']['ClientConfig'], **self.changes()})
        self.update_state(self.latest_state or self.snapshot)
        self.update_save_status()
        self.autosave.start(600)

    def retry_autosave(self):
        self.save_error = None
        self.save()

    def language_restart(self):
        preference = self.snapshot['saved']['ClientConfig']['ui_language'] if self.snapshot else self.startup_language
        return (system_language() if preference == 'auto' else preference) != self.startup_language

    def update_save_status(self):
        self.retry_save.setVisible(bool(self.save_error))
        if self.save_error:
            self.save_status.setText(label('autosave_failed') + self.save_error)
        elif self.input_errors:
            self.save_status.setText(label('autosave_invalid') + ' · '.join(self.input_errors.values()))
        elif self.changes():
            self.save_status.setText(label('autosave_waiting'))
        else:
            self.save_status.setText(label('language_restart') if self.language_restart() else label('autosave_help'))

    def capture_baseline(self):
        self.form_baseline = {name: get_value(widget, spec) for name, (widget, spec) in self.fields.items()}

    def show_report(self, result):
        lines = []
        for record in result:
            lines.append(f"{record.get('timestamp', '')}  [{record.get('level', '')}]  {record.get('message', '')}")
            details = {key: value for key, value in record.items() if key not in ('timestamp', 'level', 'message', 'content')}
            if details:
                lines.append(json.dumps(details, ensure_ascii=False))
        self.report.setPlainText('\n\n'.join(lines) or label('diagnostic_no_events'))
        self.copy_report.setEnabled(bool(lines))

    def open_advanced(self):
        # File launch belongs to the parent adapter; never execute a Python association.
        self.request('advanced', {'file': 'client'}, lambda _: None)

    @property
    def blocked(self):
        return self.busy and (not self.quiet or self.pending_request is not None)

    def enable_editor(self, enabled):
        self.pages.setEnabled(enabled)
        self.resolve_button.setEnabled(enabled)
        self.advanced.setEnabled(enabled)
        self.update_llm_controls()

    def request(self, method, params, callback, quiet=False):
        if self.closed.is_set() or (self.exiting and method != 'desktop_stop'):
            return False
        if self.busy:
            if not quiet and not self.blocked:
                # One foreground action takes priority over further polling.
                self.pending_request = (method, params, callback)
                if not method.startswith('history_'):
                    self.enable_editor(False)
                return True
            return False
        self.busy = True
        self.quiet = quiet
        self.active_method = method
        if not quiet and not method.startswith('history_'):
            self.enable_editor(False)
        self.requests.put_nowait((method, params, callback, quiet))
        return True

    def work(self):
        while not self.closed.is_set():
            try:
                method, params, callback, quiet = self.requests.get(timeout=0.2)
            except Empty:
                continue
            try:
                result, error = self.backend.dispatch(method, params), None
            except Disconnected:
                result, error = None, Disconnected
            except Exception as exc:
                result, error = None, str(exc) if isinstance(exc, RemoteError) else safe_error(exc)
            if not self.closed.is_set():
                self.responses.put((result, error, callback, quiet))

    def deliver(self):
        try:
            result, error, callback, quiet = self.responses.get_nowait()
        except Empty:
            return
        self.busy = False
        if not quiet and not self.active_method.startswith('history_'):
            self.enable_editor(True)
        if error is Disconnected:
            self.closed.set()
            self.close()
            return
        if error:
            if self.active_method == 'input_devices':
                self.devices_loaded({'devices': [], 'default': -1, 'partial': False, 'error': 'unavailable'})
            if self.active_method == 'catalog_save':
                self.exit_pending = False
                self.close_pending = False
                self.provider_status.setText(label('provider_save_failed') + error)
                self.provider_status.show()
            if self.active_method == 'save':
                self.save_error = error
                self.autosave.stop()
                self.exit_pending = False
                self.close_pending = False
                self.update_save_status()
            if self.active_method.startswith('history_'):
                getattr(callback, 'on_error', self.history.show_error)(error)
            if self.active_method.startswith('dashboard_'):
                callback.on_error(error)
            if self.active_method == 'desktop_stop':
                self.exiting = False
                self.history.stopped = False
                self.home.recent.resume()
                self.dashboard.stopped = False
                self.exit_button.setEnabled(True)
                self.home.setEnabled(True)
                self.navigation.setEnabled(True)
                self.home_button.setEnabled(True)
                self.history_button.setEnabled(True)
            if not self.active_method.startswith('history_'):
                self.status.setText(label('failed') + error)
                self.home.detail.setText(label('failed') + error)
            if not quiet and not self.active_method.startswith('history_') and self.active_method != 'catalog_save':
                if self.desktop_mode:
                    show_window(self)
                QMessageBox.warning(self, label('failed'), error)
        else:
            callback(result)
        if self.exit_pending:
            if self.pending_request and self.pending_request[0] == 'catalog_save':
                pending, self.pending_request = self.pending_request, None
                self.request(*pending)
                return
            self.pending_request = None
            self.continue_exit()
            return
        if self.close_pending and not self.busy and self.pending_request is None and not self.changes():
            self.close_pending = False
            self.close()
            return
        if self.pending_request is not None and not self.closed.is_set():
            pending, self.pending_request = self.pending_request, None
            self.request(*pending)

    def closeEvent(self, event):
        if self.closed.is_set():
            self.device_watch.stop()
            self.device_retry.stop()
            self.autosave.stop()
            self.dashboard.stop()
            self.history.stop_queries()
            self.home.recent.stop()
            self.timer.stop()
            self.poll.stop()
            event.accept()
            return
        if self.desktop_mode:
            event.ignore()
            if self.tray is not None and self.tray.isVisible():
                self.hide()
            else:
                self.request_exit()
            return
        saving = self.active_method in ('save', 'catalog_save', 'input_devices', 'dashboard_read', 'dashboard_copy')
        queued_save = self.pending_request and self.pending_request[0] in ('save', 'catalog_save')
        if self.busy and (saving or queued_save):
            event.ignore()
            self.close_pending = True
            self.status.setText(label('wait'))
            return
        if self.changes() and not self.input_errors and not self.save_error and not self.config_conflict:
            event.ignore()
            self.close_pending = True
            self.save()
            self.status.setText(label('wait'))
            return
        if (self.changes() or any(self.entry_dirty(kind) for kind in self.editors)) and not self.confirm_discard():
            event.ignore()
            return
        self.closed.set()
        self.device_watch.stop()
        self.device_retry.stop()
        self.autosave.stop()
        self.dashboard.stop()
        self.history.stop_queries()
        self.home.recent.stop()
        self.timer.stop()
        self.poll.stop()
        self.thread.join(timeout=0.5)
        event.accept()

    def request_exit(self):
        if self.exiting or self.exit_pending or self.confirming_exit or self.closed.is_set():
            return
        self.confirming_exit = True
        try:
            if not self.confirm_exit():
                return
        finally:
            self.confirming_exit = False
        self.discard_on_exit = bool(self.input_errors or self.save_error or self.config_conflict)
        provider_saving = self.busy and (self.active_method == 'catalog_save' or
                          (self.pending_request and self.pending_request[0] == 'catalog_save'))
        if ((self.discard_on_exit and self.changes()) or
                (not provider_saving and any(self.entry_dirty(kind) for kind in self.editors))) \
                and not self.confirm_discard():
            return
        self.exit_pending = True
        self.continue_exit()

    def confirm_exit(self):
        message = label('exit_confirm')
        runtime = (self.latest_state or {}).get('runtime') or {}
        if runtime.get('recording') or runtime.get('processing_count') or runtime.get('file_active'):
            message += '\n\n' + label('exit_active')
        dialog = QMessageBox(QMessageBox.Icon.Question, label('exit_client'), message, parent=self)
        leave = dialog.addButton(label('exit_client'), QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton(label('cancel'), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        return dialog.clickedButton() is leave

    def continue_exit(self):
        if not self.busy:
            if self.changes() and not self.discard_on_exit:
                self.save()
                if not self.busy:
                    self.exit_pending = False
                return
            self.begin_exit()

    def begin_exit(self):
        self.device_watch.stop()
        self.device_retry.stop()
        self.autosave.stop()
        self.dashboard.stop()
        self.history.stop_queries()
        self.home.recent.stop()
        self.exiting = True
        self.exit_pending = False
        self.poll.stop()
        self.show_home()
        show_window(self)
        self.home.headline.setText(label('desktop_stopping'))
        self.home.detail.setText(label('shutdown_hint'))
        self.exit_button.setEnabled(False)
        self.home.setEnabled(False)
        self.navigation.setEnabled(False)
        self.home_button.setEnabled(False)
        self.history_button.setEnabled(False)
        self.request('desktop_stop', {}, lambda _: self.finish_exit())

    def finish_exit(self):
        from PySide6.QtWidgets import QApplication
        self.closed.set()
        self.thread.join(timeout=0.5)
        if self.tray:
            self.tray.hide()
        self.close()
        QApplication.instance().quit()
