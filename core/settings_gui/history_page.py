"""Explicit local searches and plain-text views of saved recognition stages."""

from PySide6.QtCore import QDate, QLocale, QPointF, QRectF, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDateEdit, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QFrame,
    QPushButton, QVBoxLayout, QWidget,
)

from core.i18n import get_language, tr
from .presentation import Choice, IntegerInput, ScrollSafeInput, card, text
from .toast import GuiToast
from .history_widgets import HistoryList, HistoryPagination, HistoryRow, date_label
from .history_view import HistoryDetail, plain_label


class CalendarInput(ScrollSafeInput, QDateEdit):
    def __init__(self, name):
        super().__init__(QDate.currentDate())
        self.setCalendarPopup(True)
        self.setKeyboardTracking(False)
        self.setDisplayFormat(tr('gui.history_date_format'))
        self.setAccessibleName(tr('gui.history_date_' + name))
        self.setLocale(QLocale('zh_CN' if get_language() == 'zh-CN' else 'en_US'))
        self.setMinimumWidth(136)

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


class HistoryPage(QWidget):
    def __init__(self, request):
        super().__init__()
        self.request = request
        self.initialized = False
        self.query = None
        self.page = 0
        self.detail_queue = []
        self.loaded_query = None
        self.copy_toast = None
        self.updating_dates = False
        self.generation = 0
        self.pending = None
        self.inflight = False
        self.active_request = None
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
        self.calendar_timer = QTimer(self)
        self.calendar_timer.setInterval(1000)
        self.calendar_timer.timeout.connect(self.refresh_relative_period)
        self.setObjectName('workspace')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(12)
        layout.addWidget(text('history', 'heading'))
        layout.addWidget(text('history_intro', 'description'))
        layout = card(layout)
        layout.setSpacing(10)
        self.search_panel = layout.parentWidget()
        self.search_panel.setAccessibleName(tr('gui.history_search_title'))
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
        dates = self.date_layout = QGridLayout()
        self.period = Choice()
        self.period.setAccessibleName(tr('gui.history_period'))
        for key in ('all', 'today', 'this_week', 'week', 'month', 'custom'):
            self.period.addItem(tr('gui.history_period_' + key), key)
        dates.addWidget(self.period, 0, 0)
        dates.setColumnStretch(0, 1)
        self.date_from = CalendarInput('from')
        self.date_to = CalendarInput('to')
        self.date_separator = QLabel(tr('gui.history_to'))
        dates.addWidget(self.date_from, 0, 1)
        dates.addWidget(self.date_separator, 0, 2)
        dates.addWidget(self.date_to, 0, 3)
        layout.addLayout(dates)
        self.period.currentIndexChanged.connect(self.change_period)
        self.period.activated.connect(self.repeat_period)
        for widget in (self.date_from, self.date_to):
            widget.dateChanged.connect(self.custom_period)
        self.period.setCurrentIndex(self.period.findData('month'))
        overview = QFrame()
        overview.setObjectName('historyOverview')
        overview_layout = QHBoxLayout(overview)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(14)
        self.total_label = plain_label()
        self.total_label.setObjectName('historyTotal')
        self.total_label.setText(tr('gui.history_results'))
        self.scope_label = plain_label()
        self.scope_label.setObjectName('muted')
        self.scope_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        overview_layout.addWidget(self.total_label, 1)
        overview_layout.addWidget(self.scope_label, 1)
        layout.insertWidget(0, overview)
        self.summary = plain_label()
        self.summary.setText(tr('gui.history_ready'))
        layout.addWidget(self.summary)
        self.progress = plain_label()
        self.progress.setObjectName('muted')
        self.progress.setMinimumHeight(self.progress.fontMetrics().lineSpacing())
        layout.addWidget(self.progress)
        self.results = HistoryList()
        self.results.setAccessibleName(tr('gui.history_results'))
        layout.addWidget(self.results, 1)
        self.pagination = HistoryPagination()
        self.position_label = plain_label()
        self.position_label.setWordWrap(False)
        self.position_label.setObjectName('muted')
        navigation = QWidget()
        paging = QHBoxLayout(navigation)
        paging.setContentsMargins(0, 0, 0, 0)
        paging.setSpacing(4)
        self.previous = QPushButton('‹')
        self.next = QPushButton('›')
        self.previous.clicked.connect(lambda: self.fetch(max(0, self.target_page - 1)))
        self.next.clicked.connect(lambda: self.fetch(min(self.page_number.maximum() - 1, self.target_page + 1)))
        paging.addWidget(self.previous)
        self.page_number = IntegerInput()
        self.page_number.setRange(1, 1)
        self.page_number.setKeyboardTracking(False)
        self.page_number.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.page_number.setAccessibleName(tr('gui.history_page_number'))
        self.page_number.setToolTip(tr('gui.history_page_number'))
        self.page_number.setObjectName('historyCurrentPage')
        self.page_number.setFixedWidth(38)
        self.page_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_number.editingFinished.connect(self.jump_page)
        self.page_links = QHBoxLayout()
        self.page_links.setSpacing(4)
        self.page_links.addWidget(self.page_number)
        paging.addLayout(self.page_links)
        paging.addWidget(self.next)
        for button, key in ((self.previous, 'previous'), (self.next, 'next')):
            button.setObjectName('historyPageArrow')
            button.setFixedWidth(36)
            button.setToolTip(tr('gui.history_' + key))
            button.setAccessibleName(tr('gui.history_' + key))
        self.pagination.set_controls(self.position_label, navigation)
        layout.addWidget(self.pagination)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.page_number.setEnabled(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'date_layout'):
            narrow = self.width() < 720
            if getattr(self, '_dates_narrow', None) != narrow:
                self._dates_narrow = narrow
                widgets = (self.period, self.date_from, self.date_separator, self.date_to)
                for widget in widgets:
                    self.date_layout.removeWidget(widget)
                if narrow:
                    self.date_layout.addWidget(self.period, 0, 0, 1, 3)
                    for column, widget in enumerate(widgets[1:]):
                        self.date_layout.addWidget(widget, 1, column)
                else:
                    for column, widget in enumerate(widgets):
                        self.date_layout.addWidget(widget, 0, column)
                for column in range(4):
                    self.date_layout.setColumnStretch(column, int(column == 0 or narrow and column == 2))

    def change_period(self):
        self.resolve_period()
        if self.initialized:
            self.search_history()

    def repeat_period(self):
        if not self.query_pending:
            self.search_history()

    def resolve_period(self):
        """Resolve semantic ranges at use time, preserving custom dates."""
        self.updating_dates = True
        period = self.period.currentData()
        today = QDate.currentDate()
        previous = (self.date_from.date(), self.date_to.date())
        self.date_from.setEnabled(period != 'all')
        self.date_to.setEnabled(period != 'all')
        if period in ('today', 'this_week', 'week', 'month'):
            start = {'today': today, 'this_week': today.addDays(1 - today.dayOfWeek()),
                     'week': today.addDays(-6), 'month': QDate(today.year(), today.month(), 1)}[period]
            self.date_from.setDate(start)
            self.date_to.setDate(today)
        self.updating_dates = False
        return previous != (self.date_from.date(), self.date_to.date())

    def refresh_relative_period(self):
        if self.stopped or not self.isVisible():
            return
        if self.resolve_period() or not self.initialized:
            self.search_history()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.stopped:
            self.calendar_timer.start()
            self.refresh_relative_period()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.calendar_timer.stop()
        self.dismiss_toast()

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
        page = self.page_number.value() - 1
        if page != self.target_page:
            self.fetch(page)

    def update_page_links(self, pages):
        while self.page_links.count():
            widget = self.page_links.takeAt(0).widget()
            if widget is not self.page_number:
                widget.hide()
                widget.deleteLater()
        current = self.page + 1
        neighbors = {1, pages, current - 1, current, current + 1}
        if current == 1:
            neighbors.add(3)
        if current == pages:
            neighbors.add(pages - 2)
        previous = 0
        for number in sorted(value for value in neighbors if 1 <= value <= pages):
            if previous and number - previous > 1:
                self.page_links.addWidget(QLabel('…'))
            if number == current:
                self.page_links.addWidget(self.page_number)
            else:
                button = QPushButton(str(number))
                button.setObjectName('historyPageLink')
                button.setFixedWidth(32)
                button.setAccessibleName(tr('gui.history_page_target', page=number))
                button.clicked.connect(lambda checked=False, page=number - 1: self.fetch(page))
                self.page_links.addWidget(button)
            previous = number
        self.pagination.arrange()

    def reset_search(self):
        with QSignalBlocker(self.keyword), QSignalBlocker(self.period):
            self.keyword.clear()
            self.period.setCurrentIndex(self.period.findData('all'))
        self.date_from.setEnabled(False)
        self.date_to.setEnabled(False)
        self.search_history()

    def search_history(self):
        self.auto_search.stop()
        self.resolve_period()
        query = {'keyword': self.keyword.text().strip()}
        if self.period.currentData() != 'all':
            query.update(date_from=self.date_from.date().toString('yyyy-MM-dd'),
                         date_to=self.date_to.date().toString('yyyy-MM-dd'))
        self.fetch(0, query)

    def fetch(self, page, query=None):
        if query is None and self.resolve_period():
            self.search_history()
            return
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
        # A new search/page invalidates all outstanding detail identities.
        self.generation += 1
        self.detail_queue.clear()
        self.pending = (self.generation, method, params, apply, self.show_error)
        self.flush_request()

    def flush_request(self):
        if self.stopped or self.inflight:
            return
        if self.pending is None:
            while self.detail_queue:
                row = self.detail_queue.pop(0)
                if row not in self.results.rows or not row.expanded or row.detail is not None:
                    if row in self.results.rows:
                        row.loading = False
                    continue
                params = {key: row.entry[key] for key in ('day', 'offset', 'length', 'digest')}
                self.pending = (self.generation, 'history_read', params,
                                lambda result, row=row: self.show_entry(result, row),
                                lambda error, row=row: self.detail_error(error, row))
                break
        if self.pending is None:
            return
        generation, method, params, apply, fail = self.pending
        token = object()

        def finish(result=None, error=None):
            if self.active_request is not token:
                return
            self.active_request = None
            self.inflight = False
            if not self.stopped and generation == self.generation:
                if method == 'history_query':
                    self.query_pending = False
                    self.progress.clear()
                if error:
                    fail(error)
                else:
                    apply(result)
            # Let the shared dispatcher deliver an already queued foreground action first.
            if (self.pending is not None or self.detail_queue) and not self.stopped:
                self.retry.start()

        finish.on_error = lambda error: finish(error=error)
        self.active_request = token
        if self.request(method, params, finish):
            self.inflight = True
            self.pending = None
        else:
            self.active_request = None
            self.retry.start()

    def stop_queries(self):
        self.stopped = True
        self.generation += 1
        self.inflight = False
        self.active_request = None
        self.query_pending = False
        self.pending = None
        self.detail_queue.clear()
        self.retry.stop()
        self.auto_search.stop()
        self.calendar_timer.stop()
        self.results.stop_scroll()
        self.results.cancel_anchor()
        self.dismiss_toast()

    def resume_queries(self):
        """Restore visible refresh after a rejected client exit."""
        self.stopped = False
        for row in self.results.rows:
            row.loading = False
            if row.expanded and row.detail is None:
                self.load_detail(row)
        if self.isVisible():
            self.calendar_timer.start()

    def loaded(self, result):
        same_query = self.query == self.loaded_query and self.page == result['page']
        previous_entries = [row.entry for row in self.results.rows]
        self.page = result['page']
        self.target_page = self.page
        entries = result['entries']
        if entries != previous_entries:
            existing = {self.entry_key(row.entry): row for row in self.results.rows}
            rows = []
            for entry in entries:
                row = existing.get(self.entry_key(entry))
                if row is None:
                    row = HistoryRow(entry)
                    row.toggled.connect(self.toggle_entry)
                rows.append(row)
            self.results.replace_rows(rows)
        if not entries:
            self.results.empty_hint.setText(tr('gui.history_empty'))
        if not same_query:
            self.results.cancel_anchor()
            self.results.verticalScrollBar().setValue(0)
        self.loaded_query = dict(self.query)
        for row in self.results.rows:
            row.loading = False
            if row.expanded and row.detail is None:
                self.load_detail(row)
        pages = max(1, (result['total'] + result['page_size'] - 1) // result['page_size'])
        self.total_label.setText(tr('gui.history_total', total=f"{result['total']:,}"))
        parts = [] if result['total'] else [tr('gui.history_empty')]
        scope = (date_label(self.query['date_from']) + ' — ' + date_label(self.query['date_to'])
                 if self.query.get('date_from') else tr('gui.history_period_all'))
        if self.query.get('date_from') == self.query.get('date_to') and self.query.get('date_from'):
            scope = date_label(self.query['date_from'])
        if self.query['keyword']:
            scope += ' · “' + self.query['keyword'] + '”'
        self.scope_label.setText(scope)
        first = self.page * result['page_size'] + 1 if result['total'] else 0
        last = self.page * result['page_size'] + len(result['entries']) if result['total'] else 0
        self.position_label.setText(tr('gui.history_showing', first=first, last=last, total=result['total']))
        for flag in ('limited', 'skipped'):
            if result[flag]:
                parts.append(tr('gui.history_' + flag, count=result[flag]))
        if not result['saving_enabled']:
            parts.append(tr('gui.history_saving_off'))
        self.summary.setText('\n'.join(parts))
        self.summary.setVisible(bool(parts))
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)
        with QSignalBlocker(self.page_number):
            self.page_number.setRange(1, pages)
            self.page_number.setValue(self.page + 1)
        self.page_number.setEnabled(pages > 1)
        self.update_page_links(pages)

    @staticmethod
    def entry_key(entry):
        return tuple(entry[key] for key in ('day', 'offset', 'length', 'digest'))

    def toggle_entry(self, row):
        self.results.keep_anchor(row)
        row.update_expansion()
        if row.expanded and row.detail is None and not self.query_pending:
            self.load_detail(row)

    def load_detail(self, row):
        if self.stopped or row.loading:
            return
        row.loading = True
        row.note.setText(tr('gui.history_loading_detail'))
        row.update_expansion()
        self.detail_queue.append(row)
        self.retry.start()

    def show_entry(self, result, row):
        if row not in self.results.rows:
            return
        self.results.keep_anchor()
        row.loading = False
        row.detail = HistoryDetail(result)
        row.detail.copied.connect(self.notify_copied)
        row.detail.open_requested.connect(lambda: self.open_day(row))
        row.detail.resizing.connect(self.results.keep_anchor)
        row.box.addWidget(row.detail)
        row.update_expansion()

    def detail_error(self, error, row):
        if row in self.results.rows:
            self.results.keep_anchor()
            row.loading = False
            row.note.setText(tr('gui.failed') + error)
            row.update_expansion()

    def notify_copied(self):
        if self.stopped or not self.isVisible():
            return
        if self.copy_toast is None:
            self.copy_toast = GuiToast(self.window())
        self.copy_toast.present(tr('gui.dashboard_copied'))

    def dismiss_toast(self):
        if self.copy_toast is not None:
            self.copy_toast.dismiss()

    def open_day(self, row):
        def failed(error):
            if row in self.results.rows:
                row.detail.detail_note.setText(tr('gui.failed') + error)
                row.detail.detail_note.show()
        def opened(_):
            pass
        if self.stopped:
            return
        if self.inflight or self.pending is not None or self.detail_queue:
            failed(tr('gui.history_wait'))
            return
        self.pending = (self.generation, 'history_open', {'day': row.entry['day']}, opened, failed)
        self.flush_request()

    def show_error(self, error):
        self.progress.setText(tr('gui.failed') + error)
        for row in self.results.rows:
            row.loading = False
            if row.expanded and row.detail is None:
                self.load_detail(row)
