"""Plain-text detail views owned by individual expanded history records."""

from math import ceil

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from core.i18n import tr
from .presentation import card


class StageText(QPlainTextEdit):
    """Keep short stages compact and bound the height of long saved text."""

    def setPlainText(self, value):
        super().setPlainText(value)
        self.fit_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.fit_height()

    def fit_height(self):
        metrics = self.fontMetrics()
        width = max(40, self.viewport().width() - 12)
        lines = sum(max(1, ceil(metrics.horizontalAdvance(line) / width)) for line in self.toPlainText().splitlines())
        self.setFixedHeight(min(160, max(52, 30 + metrics.lineSpacing() * lines)))


def plain_label():
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class HistoryDetail(QFrame):
    copied = Signal()
    open_requested = Signal()
    resizing = Signal()

    def __init__(self, result):
        super().__init__()
        self.setObjectName('historyDetail')
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('historyTabs')
        box.addWidget(self.tabs)
        self.stage_fields = {}
        final = QWidget()
        final_box = QVBoxLayout(final)
        self.final_result = StageText()
        self.final_result.setReadOnly(True)
        self.final_result.setAccessibleName(tr('gui.history_tab_final'))
        final_box.addWidget(self.final_result)
        self.tabs.addTab(final, tr('gui.history_tab_final'))
        for tab, fields in (('flow', ('original', 'input', 'output', 'final')),
                            ('request', ('prompt', 'context'))):
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 8, 0, 0)
            if tab == 'request':
                self.request_info = plain_label()
                layout.addWidget(self.request_info)
            for key in fields:
                group = card(layout, 'history_stage_' + key)
                group.setContentsMargins(12, 10, 12, 10)
                group.setSpacing(6)
                field = StageText()
                field.setReadOnly(True)
                field.setAccessibleName(tr('gui.history_stage_' + key))
                self.stage_fields[key] = field
                group.addWidget(field)
            self.tabs.addTab(content, tr('gui.history_tab_' + tab))
        accounting = QWidget()
        accounting_box = QVBoxLayout(accounting)
        self.record_meta = plain_label()
        self.cost_detail = plain_label()
        accounting_box.addWidget(self.record_meta)
        accounting_box.addWidget(self.cost_detail)
        self.tabs.addTab(accounting, tr('gui.history_cost'))
        raw = QWidget()
        raw_box = QVBoxLayout(raw)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(240)
        self.detail.setAccessibleName(tr('gui.history_detail'))
        raw_box.addWidget(self.detail)
        self.copy = QPushButton(tr('gui.history_copy'))
        self.copy.clicked.connect(self.copy_entry)
        raw_box.addWidget(self.copy)
        self.tabs.addTab(raw, tr('gui.history_tab_raw'))
        self.detail_note = plain_label()
        box.addWidget(self.detail_note)
        actions = QHBoxLayout()
        self.open_file = QPushButton(tr('gui.history_open'))
        self.open_file.clicked.connect(self.open_requested)
        self.copy_final = QPushButton(tr('gui.history_copy_final'))
        self.copy_final.setObjectName('primary')
        self.copy_final.clicked.connect(self.copy_result)
        actions.addStretch()
        actions.addWidget(self.open_file)
        actions.addWidget(self.copy_final)
        box.addLayout(actions)
        self.populate(result)
        self.tabs.currentChanged.connect(self.fit_tabs)
        self.fit_tabs()

    def populate(self, result):
        self.detail.setPlainText(result['text'])
        stages = result['stages']
        self.final_text = stages['final']
        self.final_result.setPlainText(self.final_text)
        cost = result.get('cost', {'state': 'unlinked'})
        cost_text = self.format_cost(cost)
        self.record_meta.setText(tr('gui.history_outcome_' + (stages.get('outcome') or 'unknown')))
        for key, field in self.stage_fields.items():
            value = stages.get(key)
            field.setPlainText(value if value is not None else tr('gui.history_missing_' + key))
        self.request_info.setText(tr('gui.history_request_info', preset=stages.get('preset') or '—',
                                     request=stages.get('request') or '—'))
        self.cost_detail.setText(cost_text + '\n' + self.format_usage(cost))
        self.detail_note.setText(tr('gui.history_truncated') if result['truncated'] else '')
        self.detail_note.setVisible(result['truncated'])
        self.copy.setEnabled(bool(result['text']))
        self.copy_final.setEnabled(bool(self.final_text))

    def fit_tabs(self):
        self.resizing.emit()
        panel = self.tabs.currentWidget()
        width = max(80, self.tabs.width() - 4)
        height = max(panel.sizeHint().height(), panel.layout().totalHeightForWidth(width))
        self.tabs.setFixedHeight(height + self.tabs.tabBar().sizeHint().height() + 8)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.fit_tabs()

    def copy_entry(self):
        QApplication.clipboard().setText(self.detail.toPlainText())
        self.copied.emit()

    def copy_result(self):
        QApplication.clipboard().setText(self.final_text)
        self.copied.emit()

    @staticmethod
    def format_cost(cost):
        if cost['state'] != 'found':
            return tr('gui.history_cost_' + cost['state'])
        source = cost['source']
        if source == 'not_sent':
            return tr('gui.history_cost_not_sent')
        amount = cost['amount']
        if amount is None or not cost['currency'] or source == 'unknown':
            return tr('gui.history_cost_unknown')
        return tr('gui.history_cost_amount', amount=amount, currency=cost['currency'],
                  source=tr('gui.history_cost_' + source))

    @staticmethod
    def format_usage(cost):
        if cost['state'] != 'found':
            return tr('gui.history_cost_hint')
        usage = cost['usage']
        parts = [tr('gui.history_model', provider=cost['provider'] or '—', model=cost['model'] or '—')]
        if cost['status'] in ('completed', 'failed', 'cancelled', 'not_sent', 'unfinished'):
            parts.append(tr('gui.history_request_status', status=tr('cost.status.' + cost['status'])))
        parts.append(tr('gui.history_usage', input=usage.get('input_tokens', '—'), output=usage.get('output_tokens', '—'),
                        source=tr('gui.history_usage_' + cost['usage_source'])))
        for key in ('cached_tokens', 'cache_write_tokens', 'reasoning_tokens'):
            if key in usage:
                parts.append(tr('gui.history_' + key, count=usage[key]))
        if cost['elapsed_ms'] is not None:
            parts.append(tr('gui.history_elapsed', seconds=f"{cost['elapsed_ms'] / 1000:.2f}"))
        parts.append(tr('gui.history_cost_hint'))
        return '\n'.join(parts)
