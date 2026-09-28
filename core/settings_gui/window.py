"""Qt settings window. All widgets live on the process main thread."""

from datetime import datetime
import json
from queue import Empty, Queue
import threading
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from core.i18n import tr
from .backend import safe_error
from .bridge import Disconnected, RemoteError
from .fields import PAGES
from .history_page import HistoryPage
from .presentation import GROUP_STARTS, Choice, DecimalInput, HomePage, IntegerInput, Toggle, apply_theme, card, text
from .shell import show_window


def label(key):
    return tr('gui.' + key)


def make_input(name, kind):
    if kind is bool:
        widget = Toggle()
        widget.setFixedSize(widget.sizeHint())
    elif isinstance(kind, tuple):
        widget = Choice()
        for choice in kind:
            widget.addItem(label('choice.' + choice) if choice in ('minimal', 'natural', 'fluent', 'custom', 'correction',
                                                                 'auto', 'zh-CN', 'en')
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
        self.catalog = None
        self.fields = {}
        self.field_states = {}
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
        self.home = HomePage(self.navigate, self.home_action, self.start_desktop)
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
            content_layout.setSpacing(18)
            heading = text('page.' + page, 'heading')
            content_layout.addWidget(heading)
            description = text('intro.' + page, 'description')
            content_layout.addWidget(description)
            for name, kind in fields.items():
                if name in GROUP_STARTS:
                    group = card(content_layout, GROUP_STARTS[name])
                    form = QFormLayout()
                    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                    form.setHorizontalSpacing(20)
                    form.setVerticalSpacing(12)
                    group.addLayout(form)
                widget = make_input(name, kind)
                if name == 'llm_default_preset':
                    widget = Choice()
                    widget.setAccessibleName(label(name))
                    widget.addItem(label('triggers_only'), None)
                self.fields[name] = (widget, kind)
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                if kind is bool:
                    row_layout.addStretch()
                row_layout.addWidget(widget, 1)
                state = QLabel()
                state.setObjectName('state')
                self.field_states[name] = state
                row_layout.addWidget(state)
                caption = QLabel(label(name))
                caption.setWordWrap(True)
                caption.setMinimumWidth(220)
                caption.setMaximumWidth(265)
                caption.setBuddy(widget)
                form.addRow(caption, row)
            if page == 'text':
                self.add_catalog_editor(content_layout, 'presets')
                self.preview_button = QPushButton(label('preview'))
                self.preview_button.clicked.connect(self.preview)
                content_layout.addWidget(self.preview_button)
                self.prompt = QPlainTextEdit()
                self.prompt.setReadOnly(True)
                self.prompt.setAccessibleName(label('preview'))
                self.prompt.setMinimumHeight(200)
                content_layout.addWidget(self.prompt)
            if page == 'services':
                self.add_catalog_editor(content_layout, 'providers')
            if page == 'diagnostics':
                self.runtime = QLabel(label('standalone'))
                self.runtime.setWordWrap(True)
                content_layout.addWidget(self.runtime)
                runtime_actions = QHBoxLayout()
                self.runtime_buttons = []
                for action in ('toggle_pause', 'reconnect_microphone'):
                    button = QPushButton(label(action))
                    button.setEnabled(False)
                    button.clicked.connect(lambda _checked=False, action=action: self.request(
                        'action', {'name': action}, lambda result: self.status.setText(result or label('action_sent'))))
                    self.runtime_buttons.append(button)
                    runtime_actions.addWidget(button)
                content_layout.addLayout(runtime_actions)
                actions = QHBoxLayout()
                diagnostic = QPushButton(label('recent'))
                diagnostic.clicked.connect(lambda: self.request('diagnostics', {}, self.show_report))
                actions.addWidget(diagnostic)
                self.month = QLineEdit(datetime.now().strftime('%Y-%m'))
                self.month.setAccessibleName(label('month'))
                self.month.setMaximumWidth(100)
                actions.addWidget(self.month)
                costs = QPushButton(label('costs'))
                costs.clicked.connect(lambda: self.request('costs', {'month': self.month.text()}, self.show_report))
                actions.addWidget(costs)
                content_layout.addLayout(actions)
                self.report = QPlainTextEdit()
                self.report.setReadOnly(True)
                self.report.setAccessibleName(label('report'))
                self.report.setMinimumHeight(260)
                content_layout.addWidget(self.report)
            content_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            self.pages.addWidget(scroll)
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
        footer = QHBoxLayout()
        self.advanced = QPushButton(label('advanced'))
        self.advanced.setObjectName('subtle')
        self.advanced.clicked.connect(self.open_advanced)
        footer.addWidget(self.advanced)
        footer.addStretch()
        self.resolve_button = QPushButton(label('use_file_version'))
        self.resolve_button.clicked.connect(self.reload)
        self.resolve_button.hide()
        footer_layout.addWidget(self.resolve_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.save_button = QPushButton(label('save'))
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self.save)
        footer.addWidget(self.save_button)
        footer_layout.addLayout(footer)
        layout.addWidget(footer_widget)
        self.history = HistoryPage(self.request)
        self.workspace.addWidget(self.history)
        for button in self.findChildren(QPushButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_home()
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save)
        self.thread = threading.Thread(target=self.work, daemon=True, name='settings-worker')
        self.thread.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.deliver)
        self.timer.start(50)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.poll_state)
        self.poll.start(200)
        self.request('read', {}, self.loaded)

    def add_catalog_editor(self, layout, kind):
        layout = card(layout, kind)
        selector = Choice()
        selector.setAccessibleName(label(kind))
        layout.addWidget(selector)
        specs = ({'name': str, 'provider': str, 'prompt_mode': ('correction', 'custom'),
                  'triggers': str, 'use_caret_context': bool, 'temperature': float,
                  'max_tokens': int, 'system_prompt': str} if kind == 'presets' else
                 {'kind': ('openai', 'ollama'), 'base_url': str, 'model': str,
                  'timeout': float, 'api_key_env': str, 'api_key': str})
        form = QFormLayout()
        form.setVerticalSpacing(14)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)
        widgets = {}
        identifier = QLineEdit()
        identifier.setAccessibleName(label('identifier'))
        form.addRow(label('identifier'), identifier)
        for name, spec in specs.items():
            widget = QPlainTextEdit() if name in ('system_prompt', 'triggers') else make_input(name, spec)
            if name == 'provider':
                widget = Choice()
                widget.setAccessibleName(label(name))
            if name == 'triggers':
                widget.setMaximumHeight(70)
                widget.setAccessibleName(label(name))
            if name == 'system_prompt':
                widget.setMaximumHeight(150)
                widget.setAccessibleName(label(name))
            if name == 'api_key':
                widget.setEchoMode(QLineEdit.EchoMode.Password)
                widget.setPlaceholderText(label('key_keep'))
            caption = QLabel(label(name))
            caption.setBuddy(widget)
            form.addRow(caption, widget)
            widgets[name] = (widget, spec)
        clear_key = QCheckBox(label('key_clear')) if kind == 'providers' else None
        if clear_key:
            form.addRow(clear_key)
        buttons = QHBoxLayout()
        for key, callback in [('new', lambda: self.new_entry(kind)),
                              ('delete', lambda: self.save_entry(kind, delete=True)),
                              ('save_entry', lambda: self.save_entry(kind))]:
            button = QPushButton(label(key))
            button.setObjectName('primary' if key == 'save_entry' else 'danger' if key == 'delete' else '')
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.editors[kind] = (selector, identifier, widgets, clear_key)
        selector.currentTextChanged.connect(lambda _: self.select_entry(kind))
        if kind == 'presets':
            widgets['prompt_mode'][0].currentIndexChanged.connect(self.prompt_mode_changed)

    def prompt_mode_changed(self):
        widgets = self.editors['presets'][2]
        widgets['system_prompt'][0].setEnabled(widgets['prompt_mode'][0].currentData() == 'custom')

    def entry_values(self, kind):
        _, identifier, widgets, clear_key = self.editors[kind]
        values = {name: get_value(widget, spec) for name, (widget, spec) in widgets.items()}
        if kind == 'presets':
            values['triggers'] = [v.strip() for v in values['triggers'].splitlines() if v.strip()]
            if values['prompt_mode'] == 'correction':
                values.pop('system_prompt')
        else:
            if clear_key.isChecked():
                values['api_key'] = ''
            elif not values['api_key']:
                values.pop('api_key')
        return identifier.text(), values

    def entry_dirty(self, kind):
        return kind in self.editor_original and self.entry_values(kind) != self.editor_original[kind]

    def entry_changes(self, kind):
        identifier, values = self.entry_values(kind)
        if not self.editor_ids.get(kind):
            return identifier, values
        original = self.editor_original[kind][1]
        return identifier, {key: value for key, value in values.items() if value != original.get(key)}

    def confirm_discard(self):
        return QMessageBox.question(self, label('unsaved'), label('discard'),
                                    QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Discard

    def select_entry(self, kind):
        if self.loading or not self.catalog:
            return
        selector = self.editors[kind][0]
        if self.entry_dirty(kind) and not self.confirm_discard():
            selector.blockSignals(True)
            selector.setCurrentText(self.editor_ids.get(kind, ''))
            selector.blockSignals(False)
            return
        self.populate_entry(kind, selector.currentText())

    def populate_entry(self, kind, identifier):
        _, id_field, widgets, clear_key = self.editors[kind]
        self.editor_ids[kind] = identifier
        id_field.setText(identifier)
        id_field.setReadOnly(bool(identifier))
        defaults = ({'name': '', 'provider': next(iter(self.catalog['providers']), ''), 'prompt_mode': 'custom',
                     'triggers': [], 'use_caret_context': False, 'temperature': 0.0,
                     'max_tokens': 2048, 'system_prompt': ''} if kind == 'presets' else
                    {'kind': 'openai', 'base_url': '', 'model': '', 'timeout': 30.0,
                     'api_key_env': '', 'api_key': ''})
        defaults.update(self.catalog[kind].get(identifier, {}))
        for name, (widget, spec) in widgets.items():
            value = defaults.get(name, '')
            if name == 'triggers':
                value = '\n'.join(value)
            set_value(widget, spec, value)
        if clear_key:
            clear_key.setChecked(False)
            widgets['api_key'][0].setPlaceholderText(label('key_present' if defaults.get('has_api_key') else 'key_keep'))
        self.editor_original[kind] = self.entry_values(kind)
        if kind == 'presets':
            self.prompt_mode_changed()

    def new_entry(self, kind):
        if self.catalog and (not self.entry_dirty(kind) or self.confirm_discard()):
            self.populate_entry(kind, '')
            self.editors[kind][1].setFocus()

    def save_entry(self, kind, delete=False):
        if self.blocked or not self.catalog:
            return
        identifier, changes = self.entry_changes(kind)
        if delete and QMessageBox.question(self, label('delete'), label('delete_confirm'),
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.request('catalog_save', {'kind': kind, 'identifier': identifier, 'changes': changes,
                     'revision': self.catalog['revision'], 'delete': delete},
                     lambda result: self.catalog_saved(result, kind, identifier))

    def catalog_saved(self, result, kind, identifier):
        # Keep an unsaved draft in the other editor; advance its revision only after
        # our own successful transaction (external writes are rejected by the backend).
        self.catalog = result
        self.update_choices()
        self.fill_selector(kind, identifier)
        self.status.setText(label('catalog_saved'))

    def fill_selector(self, kind, identifier=None):
        selector = self.editors[kind][0]
        self.loading = True
        selector.clear()
        selector.addItems(list(self.catalog[kind]))
        if identifier in self.catalog[kind]:
            selector.setCurrentText(identifier)
        self.loading = False
        self.populate_entry(kind, selector.currentText())

    def catalog_loaded(self, result):
        self.catalog = result
        self.catalog_conflict = False
        self.update_choices()
        for kind in self.editors:
            self.fill_selector(kind, self.editor_ids.get(kind))
        self.update_conflict()

    def update_choices(self):
        for widget, entries, empty in (
            (self.fields['llm_default_preset'][0], self.catalog['presets'], True),
            (self.editors['presets'][2]['provider'][0], self.catalog['providers'], False),
        ):
            current = widget.currentData()
            widget.clear()
            if empty:
                widget.addItem(label('triggers_only'), None)
            for entry in entries:
                widget.addItem(entry, entry)
            if current is not None:
                set_value(widget, str, current)

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
                set_value(widget, spec, result['saved']['ClientConfig'].get(name))
            self.capture_baseline()

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
            elif qualified in (result.get('restart_required') or []):
                message = 'restart'
            elif qualified in (result.get('pending') or []):
                message = 'pending'
            else:
                message = 'effective' if result.get('effective') else 'saved'
            state.setText(label(message))
            state.setVisible(message not in ('saved', 'effective'))
            changed = message in ('unsaved', 'restart', 'pending')
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

    def poll_state(self):
        if self.busy or self.exiting:
            return
        if time.monotonic() >= self.next_settings_poll:
            self.next_settings_poll = time.monotonic() + 2
            self.request('read', {}, self.polled, quiet=True)
        elif self.desktop_mode:
            self.request('status', {}, self.update_runtime, quiet=True)

    def navigate(self, page):
        if page < 0:
            return
        self.pages.setCurrentIndex(page)
        self.workspace.setCurrentIndex(1)
        self.navigation.setCurrentRow(page)
        self.update_navigation()

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
        if self.blocked or not self.snapshot or not self.snapshot.get('saved'):
            return
        self.request('save', {'changes': self.changes(), 'revision': self.snapshot['revision']}, self.saved)

    def saved(self, result):
        self.snapshot = result
        self.capture_baseline()
        self.update_state(result)
        self.status.setText(label('settings_saved'))

    def capture_baseline(self):
        self.form_baseline = {name: get_value(widget, spec) for name, (widget, spec) in self.fields.items()}

    def preview(self):
        if self.blocked or not self.catalog or not self.snapshot:
            return
        identifier, changes = self.entry_changes('presets')
        self.request('preview', {'changes': self.changes(), 'revision': self.snapshot['revision'],
                     'draft': {'kind': 'presets', 'identifier': identifier, 'changes': changes,
                               'revision': self.catalog['revision']}},
                     lambda result: self.prompt.setPlainText(
                         label('context_on' if result['context_allowed'] else 'context_off') + '\n\n' + result['system_prompt']))

    def show_report(self, result):
        self.report.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))

    def open_advanced(self):
        # File launch belongs to the parent adapter; never execute a Python association.
        self.request('advanced', {'file': 'client'}, lambda _: None)

    @property
    def blocked(self):
        return self.busy and (not self.quiet or self.pending_request is not None)

    def enable_editor(self, enabled):
        self.pages.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.resolve_button.setEnabled(enabled)
        self.advanced.setEnabled(enabled)

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
        if self.exit_pending:
            self.pending_request = None
            self.begin_exit()
            return
        if error is Disconnected:
            self.closed.set()
            self.close()
            return
        if error:
            if self.active_method.startswith('history_'):
                getattr(callback, 'on_error', self.history.show_error)(error)
            if self.active_method == 'desktop_stop':
                self.exiting = False
                self.history.stopped = False
                self.exit_button.setEnabled(True)
                self.home.setEnabled(True)
                self.navigation.setEnabled(True)
                self.home_button.setEnabled(True)
                self.history_button.setEnabled(True)
            if not self.active_method.startswith('history_'):
                self.status.setText(label('failed') + error)
                self.home.detail.setText(label('failed') + error)
            if not quiet and not self.active_method.startswith('history_'):
                if self.desktop_mode:
                    show_window(self)
                QMessageBox.warning(self, label('failed'), error)
        else:
            callback(result)
        if self.pending_request is not None and not self.closed.is_set():
            pending, self.pending_request = self.pending_request, None
            self.request(*pending)

    def closeEvent(self, event):
        if self.closed.is_set():
            self.history.stop_queries()
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
        saving = self.active_method in ('save', 'catalog_save')
        queued_save = self.pending_request and self.pending_request[0] in ('save', 'catalog_save')
        if self.busy and (saving or queued_save):
            event.ignore()
            self.status.setText(label('wait'))
            return
        if (self.changes() or any(self.entry_dirty(kind) for kind in self.editors)) and not self.confirm_discard():
            event.ignore()
            return
        self.closed.set()
        self.history.stop_queries()
        self.timer.stop()
        self.poll.stop()
        self.thread.join(timeout=0.5)
        event.accept()

    def request_exit(self):
        if self.exiting or self.exit_pending or self.closed.is_set():
            return
        if (self.changes() or any(self.entry_dirty(kind) for kind in self.editors)) and not self.confirm_discard():
            return
        self.exit_pending = True
        if not self.busy:
            self.begin_exit()

    def begin_exit(self):
        self.history.stop_queries()
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
