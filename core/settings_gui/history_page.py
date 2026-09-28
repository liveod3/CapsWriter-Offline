"""Explicit local searches and plain-text views of saved recognition stages."""

from math import ceil

from PySide6.QtCore import QDate, QLocale, QPointF, QRectF, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QDateEdit, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from core.i18n import get_language, tr
from .presentation import Choice, IntegerInput, ScrollSafeInput, card, text


class CalendarInput(ScrollSafeInput, QDateEdit):
    def __init__(self, name):
        super().__init__(QDate.currentDate())
        self.setCalendarPopup(True)
        self.setKeyboardTracking(False)
        self.setDisplayFormat('yyyy-MM-dd')
        self.setAccessibleName(tr('gui.history_date_' + name))
        self.setLocale(QLocale('zh_CN' if get_language() == 'zh-CN' else 'en_US'))
        self.setMinimumWidth(122)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#70778b' if self.isEnabled() else '#b8bfd0'), 1.2))
        x, y = self.width() - 12, self.height() / 2
        painter.drawRoundedRect(QRectF(x - 6, y - 5, 12, 11), 1, 1)
        painter.drawLine(QPointF(x - 6, y - 1), QPointF(x + 6, y - 1))
        for offset in (-3, 3):
            painter.drawLine(QPointF(x + offset, y - 7), QPointF(x + offset, y - 3))


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


class HistoryPage(QWidget):
    def __init__(self, request):
        super().__init__()
        self.request = request
        self.initialized = False
        self.query = None
        self.page = 0
        self.entry = None
        self.final_text = ''
        self.updating_dates = False
        self.generation = 0
        self.pending = None
        self.inflight = False
        self.query_pending = False
        self.stopped = False
        self.target_page = 0
        self.auto_search = QTimer(self)
        self.auto_search.setSingleShot(True)
        self.auto_search.setInterval(300)
        self.auto_search.timeout.connect(self.search_history)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.setInterval(50)
        self.retry.timeout.connect(self.flush_request)
        self.setObjectName('workspace')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(12)
        layout.addWidget(text('history', 'heading'))
        layout.addWidget(text('history_intro', 'description'))
        filters = QHBoxLayout()
        self.keyword = QLineEdit()
        self.keyword.setMaxLength(200)
        self.keyword.setPlaceholderText(tr('gui.history_keyword'))
        self.keyword.setAccessibleName(tr('gui.history_keyword'))
        self.keyword.setClearButtonEnabled(True)
        filters.addWidget(self.keyword, 1)
        self.search = QPushButton(tr('gui.history_refresh'))
        self.search.setObjectName('primary')
        self.search.clicked.connect(self.search_history)
        self.keyword.returnPressed.connect(self.search_history)
        self.keyword.textChanged.connect(self.schedule_search)
        filters.addWidget(self.search)
        self.reset = QPushButton(tr('gui.history_reset'))
        self.reset.clicked.connect(self.reset_search)
        filters.addWidget(self.reset)
        layout.addLayout(filters)
        dates = QHBoxLayout()
        self.period = Choice()
        self.period.setAccessibleName(tr('gui.history_period'))
        for key in ('all', 'today', 'week', 'month', 'custom'):
            self.period.addItem(tr('gui.history_period_' + key), key)
        dates.addWidget(self.period, 1)
        self.date_from = CalendarInput('from')
        self.date_to = CalendarInput('to')
        dates.addWidget(self.date_from)
        dates.addWidget(QLabel(tr('gui.history_to')))
        dates.addWidget(self.date_to)
        layout.addLayout(dates)
        self.period.currentIndexChanged.connect(self.change_period)
        for widget in (self.date_from, self.date_to):
            widget.dateChanged.connect(self.custom_period)
        self.period.setCurrentIndex(self.period.findData('month'))
        self.summary = plain_label()
        self.summary.setText(tr('gui.history_ready'))
        layout.addWidget(self.summary)
        self.progress = plain_label()
        self.progress.setObjectName('muted')
        self.progress.setMinimumHeight(self.progress.fontMetrics().lineSpacing())
        layout.addWidget(self.progress)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.results = QListWidget()
        self.results.setAccessibleName(tr('gui.history_results'))
        self.results.setWordWrap(True)
        self.results.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setMinimumWidth(155)
        left_layout.addWidget(self.results, 1)
        paging = QHBoxLayout()
        self.previous = QPushButton(tr('gui.history_previous'))
        self.next = QPushButton(tr('gui.history_next'))
        self.previous.clicked.connect(lambda: self.fetch(max(0, self.target_page - 1)))
        self.next.clicked.connect(lambda: self.fetch(min(self.page_number.maximum() - 1, self.target_page + 1)))
        paging.addWidget(self.previous)
        paging.addWidget(self.next)
        left_layout.addLayout(paging)
        jump = QHBoxLayout()
        self.page_number = IntegerInput()
        self.page_number.setRange(1, 1)
        self.page_number.setKeyboardTracking(False)
        self.page_number.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.page_number.setAccessibleName(tr('gui.history_page_number'))
        self.page_number.setToolTip(tr('gui.history_page_number'))
        self.page_number.setMinimumWidth(54)
        self.page_number.setMaximumWidth(76)
        self.jump = QPushButton(tr('gui.history_jump'))
        self.jump.clicked.connect(self.jump_page)
        self.page_number.lineEdit().returnPressed.connect(self.jump_page)
        caption = QLabel(tr('gui.history_page_label'))
        caption.setBuddy(self.page_number)
        jump.addWidget(caption)
        jump.addWidget(self.page_number, 1)
        jump.addWidget(self.jump)
        left_layout.addLayout(jump)
        split.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.record_title = plain_label()
        self.record_title.setObjectName('cardTitle')
        right_layout.addWidget(self.record_title)
        self.record_meta = plain_label()
        right_layout.addWidget(self.record_meta)
        self.tabs = QTabWidget()
        right_layout.addWidget(self.tabs, 1)
        self.empty_space = QWidget()
        self.empty_space.setObjectName('historyEmpty')
        empty_layout = QVBoxLayout(self.empty_space)
        empty_layout.setContentsMargins(28, 28, 28, 28)
        self.empty_hint = text('history_select', 'emptyHint')
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch()
        empty_layout.addWidget(self.empty_hint)
        empty_layout.addStretch()
        right_layout.addWidget(self.empty_space, 1)
        self.stage_fields = {}
        for tab, fields in (('flow', ('original', 'input', 'output', 'final')),
                            ('request', ('prompt', 'context'))):
            content = QWidget()
            content.setObjectName('workspace')
            box = QVBoxLayout(content)
            box.setContentsMargins(10, 10, 10, 10)
            if tab == 'request':
                self.request_info = plain_label()
                box.addWidget(self.request_info)
            for key in fields:
                group = card(box, 'history_stage_' + key)
                group.setContentsMargins(12, 12, 12, 12)
                group.setSpacing(8)
                field = StageText()
                field.setReadOnly(True)
                field.setAccessibleName(tr('gui.history_stage_' + key))
                self.stage_fields[key] = field
                group.addWidget(field)
            if tab == 'flow':
                self.cost_detail = plain_label()
                card(box, 'history_cost').addWidget(self.cost_detail)
            box.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            self.tabs.addTab(scroll, tr('gui.history_tab_' + tab))
        raw = QWidget()
        raw_layout = QVBoxLayout(raw)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName(tr('gui.history_detail'))
        raw_layout.addWidget(self.detail)
        self.copy = QPushButton(tr('gui.history_copy'))
        self.copy.clicked.connect(self.copy_entry)
        raw_layout.addWidget(self.copy)
        self.tabs.addTab(raw, tr('gui.history_tab_raw'))
        self.detail_note = plain_label()
        right_layout.addWidget(self.detail_note)
        actions = QHBoxLayout()
        self.open_file = QPushButton(tr('gui.history_open'))
        self.open_file.clicked.connect(self.open_day)
        self.copy_final = QPushButton(tr('gui.history_copy_final'))
        self.copy_final.setObjectName('primary')
        self.copy_final.clicked.connect(self.copy_result)
        actions.addWidget(self.open_file)
        actions.addWidget(self.copy_final)
        right_layout.addLayout(actions)
        split.addWidget(right)
        split.setSizes([240, 570])
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)
        self.results.currentItemChanged.connect(self.select_entry)
        self.clear_detail()
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.jump.setEnabled(False)
        self.page_number.setEnabled(False)

    def change_period(self):
        self.updating_dates = True
        period = self.period.currentData()
        today = QDate.currentDate()
        self.date_from.setEnabled(period != 'all')
        self.date_to.setEnabled(period != 'all')
        if period in ('today', 'week', 'month'):
            start = today if period == 'today' else today.addDays(-6) if period == 'week' else QDate(today.year(), today.month(), 1)
            self.date_from.setDate(start)
            self.date_to.setDate(today)
        self.updating_dates = False
        if self.initialized:
            self.search_history()

    def custom_period(self):
        if not self.updating_dates:
            with QSignalBlocker(self.period):
                self.period.setCurrentIndex(self.period.findData('custom'))
            self.schedule_search()

    def schedule_search(self):
        if self.initialized and not self.stopped:
            self.auto_search.start()

    def jump_page(self):
        self.page_number.interpretText()
        self.fetch(self.page_number.value() - 1)

    def reset_search(self):
        with QSignalBlocker(self.keyword), QSignalBlocker(self.period):
            self.keyword.clear()
            self.period.setCurrentIndex(self.period.findData('all'))
        self.date_from.setEnabled(False)
        self.date_to.setEnabled(False)
        self.search_history()

    def search_history(self):
        self.auto_search.stop()
        query = {'keyword': self.keyword.text().strip()}
        if self.period.currentData() != 'all':
            query.update(date_from=self.date_from.date().toString('yyyy-MM-dd'),
                         date_to=self.date_to.date().toString('yyyy-MM-dd'))
        self.fetch(0, query)

    def fetch(self, page, query=None):
        query = self.query if query is None else query
        if self.stopped or query is None:
            return
        self.query = query
        self.target_page = page
        self.initialized = True
        self.query_pending = True
        self.progress.setText(tr('gui.history_loading'))
        self.queue_request('history_query', {**query, 'page': page}, self.loaded)

    def queue_request(self, method, params, apply):
        # Only the latest navigation intent matters; never grow a request backlog.
        self.generation += 1
        self.pending = (self.generation, method, params, apply)
        self.flush_request()

    def flush_request(self):
        if self.stopped or self.inflight or self.pending is None:
            return
        generation, method, params, apply = self.pending

        def finish(result=None, error=None):
            self.inflight = False
            if not self.stopped and generation == self.generation:
                self.query_pending = False
                self.progress.clear()
                if error:
                    self.show_error(error)
                else:
                    apply(result)
            # Let the shared dispatcher deliver any already queued foreground action first.
            if self.pending is not None and not self.stopped:
                self.retry.start()

        finish.on_error = lambda error: finish(error=error)
        if self.request(method, params, finish):
            self.inflight = True
            self.pending = None
        else:
            self.retry.start()

    def stop_queries(self):
        self.stopped = True
        self.inflight = False
        self.query_pending = False
        self.pending = None
        self.retry.stop()
        self.auto_search.stop()

    def clear_detail(self):
        self.entry = None
        self.final_text = ''
        self.detail.clear()
        self.detail_note.clear()
        self.record_title.clear()
        self.record_title.hide()
        self.record_meta.hide()
        self.empty_hint.setText(tr('gui.history_select'))
        self.record_meta.clear()
        self.request_info.clear()
        self.cost_detail.clear()
        for field in self.stage_fields.values():
            field.clear()
        for button in (self.copy, self.copy_final, self.open_file):
            button.setEnabled(False)
        self.tabs.setVisible(False)
        self.empty_space.setVisible(True)

    def loaded(self, result):
        self.page = result['page']
        self.target_page = self.page
        selected = None
        with QSignalBlocker(self.results):
            self.results.clear()
            for row in result['entries']:
                item = QListWidgetItem(row['day'] + '  ' + (row['time'] or tr('gui.history_day')) + '\n' + row['preview'])
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.results.addItem(item)
                if row == self.entry:
                    selected = item
            if selected is not None:
                self.results.setCurrentItem(selected)
        if selected is None:
            self.clear_detail()
        pages = max(1, (result['total'] + result['page_size'] - 1) // result['page_size'])
        parts = [tr('gui.history_count', total=result['total'], page=self.page + 1, pages=pages)
                 if result['total'] else tr('gui.history_empty')]
        scope = (self.query['date_from'] + ' — ' + self.query['date_to'] if self.query.get('date_from')
                 else tr('gui.history_period_all'))
        if self.query['keyword']:
            scope += ' · “' + self.query['keyword'] + '”'
        parts.append(scope)
        for flag in ('limited', 'skipped'):
            if result[flag]:
                parts.append(tr('gui.history_' + flag, count=result[flag]))
        if not result['saving_enabled']:
            parts.append(tr('gui.history_saving_off'))
        self.summary.setText('\n'.join(parts))
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)
        self.page_number.setRange(1, pages)
        self.page_number.setValue(self.page + 1)
        self.page_number.setEnabled(pages > 1)
        self.jump.setEnabled(pages > 1)

    def select_entry(self, item, previous=None):
        if self.query_pending:
            return
        if not item:
            self.generation += 1
            self.pending = None
            self.progress.clear()
            self.clear_detail()
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        params = {key: entry[key] for key in ('day', 'offset', 'length', 'digest')}
        self.progress.setText(tr('gui.history_loading_detail'))
        if self.entry is None:
            self.empty_hint.setText(tr('gui.history_loading_detail'))
        self.queue_request('history_read', params, lambda result: self.show_entry(result, entry))

    def show_entry(self, result, entry):
        self.entry = entry
        self.record_title.show()
        self.record_meta.show()
        self.detail.setPlainText(result['text'])
        stages = result['stages']
        self.final_text = stages['final']
        outcome = stages.get('outcome') or 'unknown'
        self.record_title.setText(self.entry['day'] + '  ' + self.entry['time'])
        cost = result.get('cost', {'state': 'unlinked'})
        cost_text = self.format_cost(cost)
        self.record_meta.setText(tr('gui.history_outcome_' + outcome) + '\n' + cost_text)
        for key, field in self.stage_fields.items():
            value = stages.get(key)
            field.setPlainText(value if value is not None else tr('gui.history_missing_' + key))
        self.request_info.setText(tr('gui.history_request_info', preset=stages.get('preset') or '—',
                                     request=stages.get('request') or '—'))
        self.cost_detail.setText(cost_text + '\n' + self.format_usage(cost))
        self.tabs.setVisible(True)
        self.empty_space.setVisible(False)
        for index in (0, 1):
            self.tabs.widget(index).verticalScrollBar().setValue(0)
        self.detail_note.setText(tr('gui.history_truncated') if result['truncated'] else '')
        self.copy.setEnabled(bool(result['text']))
        self.copy_final.setEnabled(bool(self.final_text))
        self.open_file.setEnabled(True)

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

    def copy_entry(self):
        QApplication.clipboard().setText(self.detail.toPlainText())
        self.detail_note.setText(tr('gui.history_copied'))

    def copy_result(self):
        QApplication.clipboard().setText(self.final_text)
        self.detail_note.setText(tr('gui.history_result_copied'))

    def open_day(self):
        if self.entry:
            if not self.request('history_open', {'day': self.entry['day']}, lambda _: None):
                self.detail_note.setText(tr('gui.history_wait'))

    def show_error(self, error):
        self.progress.setText(tr('gui.failed') + error)
        self.empty_hint.setText(tr('gui.history_select'))
