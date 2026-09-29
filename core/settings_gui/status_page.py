"""Local usage cards and explicit copy actions, with visible-page refresh only."""

from decimal import Decimal

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core.i18n import tr
from .presentation import Choice, text


def plain(value='', style='muted'):
    label = QLabel(value)
    label.setObjectName(style)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


class RecordButton(QPushButton):
    """Let wrapped timestamp/preview labels determine the clickable row height."""

    def __init__(self):
        super().__init__()
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def heightForWidth(self, width):
        return max(70, self.layout().totalHeightForWidth(width) if self.layout() else 70)

    def sizeHint(self):
        return QSize(200, self.heightForWidth(self.width()))

    def minimumSizeHint(self):
        return QSize(0, 70)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)


class StatusPage(QWidget):
    def __init__(self, request, open_history):
        super().__init__()
        self.request = request
        self.pending = None
        self.inflight = False
        self.operation = None
        self.generation = 0
        self.stopped = False
        self.last_entries = None
        self.cards = []
        self.metrics = {}
        self.columns = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        actions = QHBoxLayout()
        self.updated = plain(tr('gui.dashboard_local'))
        actions.addWidget(self.updated, 1)
        self.refresh_button = QPushButton(tr('gui.history_refresh'))
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        layout.addLayout(self.grid)
        for key in ('today', 'tokens', 'spend'):
            frame = QFrame()
            frame.setObjectName('card')
            box = QVBoxLayout(frame)
            box.setContentsMargins(18, 16, 18, 16)
            box.setSpacing(8)
            box.addWidget(text('dashboard_' + key, 'muted'))
            value = plain('—', 'metric')
            hint = plain(tr('gui.dashboard_local'))
            box.addWidget(value)
            box.addWidget(hint)
            box.addStretch()
            self.cards.append(frame)
            self.metrics[key] = (value, hint)
        self.note = plain()
        layout.addWidget(self.note)
        recent = QFrame()
        recent.setObjectName('card')
        box = QVBoxLayout(recent)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(10)
        bar = QHBoxLayout()
        bar.addWidget(text('dashboard_recent', 'cardTitle'), 1)
        self.period = Choice()
        self.period.setAccessibleName(tr('gui.history_period'))
        self.period.addItem(tr('gui.dashboard_latest'), 'recent')
        self.period.addItem(tr('gui.history_period_today'), 'today')
        self.period.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.period)
        box.addLayout(bar)
        box.addWidget(text('dashboard_copy_hint', 'muted'))
        self.entries = QVBoxLayout()
        self.entries.setSpacing(8)
        box.addLayout(self.entries)
        self.empty = text('dashboard_empty', 'emptyHint')
        box.addWidget(self.empty)
        history = QPushButton(tr('gui.dashboard_history'))
        history.clicked.connect(lambda: open_history(self.period.currentData()))
        box.addWidget(history, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(recent)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.setInterval(100)
        self.retry.timeout.connect(self.flush)
        self.timer = QTimer(self)
        self.timer.setInterval(30000)
        self.timer.timeout.connect(self.refresh)
        self.reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        columns = 3 if self.width() >= 760 else 2 if self.width() >= 460 else 1
        if columns == self.columns:
            return
        self.columns = columns
        for frame in self.cards:
            self.grid.removeWidget(frame)
        for index, frame in enumerate(self.cards):
            self.grid.addWidget(frame, index // columns, index % columns, 1,
                                2 if columns == 2 and index == 2 else 1)
        for index in range(3):
            self.grid.setColumnStretch(index, 1 if index < columns else 0)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.stopped:
            self.timer.start()
            self.refresh()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()
        self.retry.stop()
        self.pending = None
        self.generation += 1

    def stop(self):
        self.stopped = True
        self.timer.stop()
        self.retry.stop()
        self.pending = None
        self.generation += 1

    def refresh(self, *_):
        if (self.inflight and self.operation == 'dashboard_copy') or (
                self.pending and self.pending[0] == 'dashboard_copy'):
            return
        self.schedule('dashboard_read', {'period': self.period.currentData()})

    def schedule(self, method, params):
        if self.stopped or not self.isVisible():
            return
        self.generation += 1
        self.pending = (method, params, self.generation)
        self.flush()

    def flush(self):
        if self.stopped or not self.isVisible() or self.inflight or not self.pending:
            return
        method, params, generation = self.pending
        def done(result):
            self.inflight = False
            if not self.stopped and self.isVisible() and generation == self.generation:
                if method == 'dashboard_copy':
                    QApplication.clipboard().setText(result)
                    self.note.setText(tr('gui.dashboard_copied'))
                else:
                    self.show_data(result)
            self.flush()
        def failed(error):
            self.inflight = False
            if not self.stopped and self.isVisible() and generation == self.generation:
                self.note.setText(tr('gui.dashboard_failed') + error)
            self.flush()
        done.on_error = failed
        if self.request(method, params, done, quiet=True):
            self.inflight = True
            self.operation = method
            self.pending = None
        else:
            self.retry.start()

    def show_data(self, result):
        self.updated.setText(tr('gui.dashboard_updated', day=result['day'], time=result['updated']))
        usage = result['usage']
        value, hint = self.metrics['today']
        value.setText(str(result['today_count']) if result['history_state'] == 'ready' else '—')
        hint.setText(tr('gui.dashboard_saved_only' if result['saving_enabled'] else 'gui.dashboard_saving_off'))
        value, hint = self.metrics['tokens']
        available = usage['state'] == 'ready'
        value.setText(f"{usage['tokens_today']:,}" if available and (
            usage['known_token_requests'] or not usage['requests_today']) else '—')
        hint.setText(tr('gui.dashboard_token_detail', input=f"{usage['input_tokens']:,}",
                        output=f"{usage['output_tokens']:,}", unknown=usage['unknown_token_requests'])
                     if available else tr('gui.dashboard_usage_' + usage['state']))
        value, hint = self.metrics['spend']
        amounts, provenance = [], []
        if available:
            for currency, bucket in sorted(usage['currencies'].items()):
                total = sum(Decimal(number) for number in bucket.values())
                amounts.append(f'{currency} {total:f}')
                provenance.append(tr('gui.dashboard_cost_detail', currency=currency,
                    reported=bucket['provider_reported'],
                    estimated=str(Decimal(bucket['rate_estimate']) + Decimal(bucket['token_estimate'])),
                    possible=bucket['possible_cost']))
        value.setText('\n'.join(amounts) or '—')
        hint.setText('\n'.join(provenance) or tr('gui.dashboard_usage_' + usage['state']))
        notes = [tr('gui.dashboard_accounting_note', month=result['month'], unknown=usage['unknown_cost_requests'])]
        if not usage['tracking_enabled']:
            notes.append(tr('gui.dashboard_tracking_off'))
        if result['history_limited'] or usage['limited'] or usage['skipped']:
            notes.append(tr('gui.dashboard_partial'))
        if result['history_state'] != 'ready':
            notes.append(tr('gui.dashboard_history_unavailable'))
        self.note.setText('\n'.join(notes))
        if result['entries'] == self.last_entries:
            return
        self.last_entries = result['entries']
        while self.entries.count():
            self.entries.takeAt(0).widget().deleteLater()
        for entry in result['entries']:
            button = RecordButton()
            button.setObjectName('recordCard')
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumWidth(0)
            row = QVBoxLayout(button)
            row.setContentsMargins(12, 10, 12, 10)
            row.setSpacing(5)
            for caption, style in ((entry['day'] + '  ' + entry['time'], 'muted'), (entry['preview'], 'recordText')):
                label = plain(caption, style)
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                row.addWidget(label)
            button.setAccessibleName(tr('gui.dashboard_copy_entry', text=entry['preview']))
            reference = {key: entry[key] for key in ('day', 'offset', 'length', 'digest')}
            button.clicked.connect(lambda _checked=False, ref=reference: self.schedule('dashboard_copy', ref))
            self.entries.addWidget(button)
        self.empty.setVisible(not result['entries'])
