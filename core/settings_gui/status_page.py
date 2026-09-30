"""Local usage cards and explicit copy actions, with visible-page refresh only."""

from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core.i18n import tr
from .presentation import Choice, text
from .statistics_view import field_label, number, recent_value


def plain(value='', style='muted'):
    label = QLabel(value)
    label.setObjectName(style)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


class TimingCard(QFrame):
    """Separate the latest observation from the selected period's distribution."""

    def __init__(self, title, metric):
        super().__init__()
        self.setObjectName('card')
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        box = QVBoxLayout(self)
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(field_label(title, 'cardTitle', 'activity.help.' + metric), 1)
        heading.addWidget(text('stats_unit', 'eyebrow'))
        box.addLayout(heading)
        latest = QFrame()
        latest.setObjectName('timingLatest')
        latest_box = QVBoxLayout(latest)
        latest_box.setContentsMargins(14, 12, 14, 12)
        bar = QHBoxLayout()
        bar.addWidget(field_label('latest_label', 'timingLabel', 'gui.stats_help_recent'), 1)
        self.badge = plain('', 'timingBadge')
        bar.addWidget(self.badge)
        latest_box.addLayout(bar)
        self.value = plain('—', 'timingValue')
        latest_box.addWidget(self.value)
        self.timestamp = plain(tr('gui.stats_no_recent'), 'timingLabel')
        latest_box.addWidget(self.timestamp)
        box.addWidget(latest)
        tiles = QGridLayout()
        tiles.setSpacing(10)
        self.values = {}
        self.tiles = []
        for index, key in enumerate(('median', 'mean', 'max', 'p95')):
            tile = QFrame()
            tile.setObjectName('timingTile')
            cell = QVBoxLayout(tile)
            cell.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            cell.setContentsMargins(12, 10, 12, 10)
            cell.setSpacing(4)
            cell.addWidget(field_label('tile_' + key, 'timingLabel',
                                      'gui.stats_help_' + ('maximum' if key == 'max' else key)))
            value = plain('—', 'timingSummary')
            value.setWordWrap(False)
            value.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
            cell.addWidget(value)
            tiles.addWidget(tile, index // 2, index % 2)
            self.values[key] = value
            self.tiles.append(tile)
        tiles.setColumnStretch(0, 1)
        tiles.setColumnStretch(1, 1)
        box.addLayout(tiles)
        self.samples = plain()
        box.addWidget(self.samples)

    def show_group(self, group):
        recent = group.get('recent')
        value, hint = recent_value(recent)
        self.value.setText(value)
        self.value.setToolTip(hint)
        self.timestamp.setText(tr('gui.stats_task_time', time=datetime.fromisoformat(
            recent['started_at']).astimezone().strftime('%m-%d %H:%M:%S')) if recent else hint)
        self.badge.setVisible(bool(recent and recent['outcome'] != 'completed'))
        if recent:
            self.badge.setText(tr('activity.outcome.' + recent['outcome']))
            tone = ('ok' if recent['outcome'] == 'completed' else
                    'error' if recent['outcome'] == 'failed' else 'neutral')
            self.badge.setProperty('tone', tone)
            self.badge.style().unpolish(self.badge)
            self.badge.style().polish(self.badge)
        count = group.get('count', 0)
        for key, label in self.values.items():
            label.setText(number(group.get(key)) + (' *' if key == 'p95' and 0 < count < 20 else ''))
        self.samples.setText(tr('gui.stats_eligible_samples', count=count))
        self.samples.setToolTip(tr('gui.stats_help_samples'))


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
        self.stats_period = Choice()
        self.stats_period.setAccessibleName(tr('gui.stats_range'))
        for key in ('today', 'week', 'month', 'year', '7d', '30d'):
            self.stats_period.addItem(tr('gui.stats_' + key), key)
        self.stats_period.setToolTip(tr('gui.stats_calendar_help'))
        self.stats_period.currentIndexChanged.connect(self.refresh)
        actions.addWidget(self.stats_period)
        self.refresh_button = QPushButton(tr('gui.history_refresh'))
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)
        self.range_label = plain()
        layout.addWidget(self.range_label)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(self.grid)
        titles = {'tasks': 'tasks_count', 'today': 'saved', 'tokens': 'tokens', 'spend': 'spend'}
        for key, title in titles.items():
            frame = QFrame()
            frame.setObjectName('card')
            frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            box = QVBoxLayout(frame)
            box.setContentsMargins(18, 16, 18, 16)
            box.setSpacing(8)
            box.addWidget(field_label(title, 'cardTitle'))
            value = plain('—', 'metric')
            hint = plain(tr('gui.dashboard_local'))
            box.addWidget(value)
            box.addWidget(hint)
            box.addStretch()
            self.cards.append(frame)
            self.metrics[key] = (value, hint)
        self.timing_grid = QGridLayout()
        self.timing_grid.setSpacing(12)
        self.timing_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.timing_cards = {key: TimingCard(key, metric) for key, metric in (
            ('wait', 'dictation.post_stop'), ('wake', 'microphone.wake'))}
        layout.addLayout(self.timing_grid)
        self.note = plain()
        layout.addWidget(self.note)
        self.timing_text = plain(tr('gui.activity_empty'))
        layout.addWidget(self.timing_text)
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
        layout.addStretch()
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
        columns = 4 if self.width() >= 1000 else 2 if self.width() >= 460 else 1
        timing_columns = 2 if self.width() >= 700 else 1
        if (columns, timing_columns) == self.columns:
            return
        self.columns = (columns, timing_columns)
        for frame in self.cards:
            self.grid.removeWidget(frame)
        for index, frame in enumerate(self.cards):
            self.grid.addWidget(frame, index // columns, index % columns)
        for index in range(4):
            self.grid.setColumnStretch(index, 1 if index < columns else 0)
        for frame in self.timing_cards.values():
            self.timing_grid.removeWidget(frame)
        for index, frame in enumerate(self.timing_cards.values()):
            self.timing_grid.addWidget(frame, index // timing_columns, index % timing_columns)
        for index in range(2):
            self.timing_grid.setColumnStretch(index, 1 if index < timing_columns else 0)

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
        self.schedule('dashboard_read', {'period': self.period.currentData(),
                                         'stats_period': self.stats_period.currentData()})

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
                    self.note.show()
                else:
                    self.show_data(result)
            self.flush()
        def failed(error):
            self.inflight = False
            if not self.stopped and self.isVisible() and generation == self.generation:
                self.note.setText(tr('gui.dashboard_failed') + error)
                self.note.show()
            self.flush()
        done.on_error = failed
        if self.request(method, params, done, quiet=True):
            self.inflight = True
            self.operation = method
            self.pending = None
        else:
            self.retry.start()

    def show_data(self, result):
        timings = result.get('timings', {})
        readable = timings.get('state') != 'unavailable'
        if not readable:
            timings = {**timings, 'groups': [], 'tasks': [], 'outcomes': {}}
        counts = timings.get('outcomes', {})
        self.timing_text.setText(tr('gui.stats_outcomes', **{key: counts.get(key, 0) for key in (
            'completed', 'fallback', 'failed', 'cancelled', 'interrupted', 'not_inserted', 'unfinished')})
            if readable else tr('gui.stats_read_failed'))
        value, hint = self.metrics['tasks']
        value.setText(str(timings.get('count', 0)) if readable else '—')
        hint.setText(tr('gui.stats_collecting' if timings.get('enabled', True) else 'gui.activity_off'))
        for key, metric in (('wait', 'dictation.post_stop'), ('wake', 'microphone.wake')):
            # Never combine measurements with different contract versions into one card.
            group = next((row for row in timings.get('groups', [])
                          if row['metric'] == metric and row.get('version', 1) == 1), {})
            self.timing_cards[key].show_group(group)
        window = result['window']
        scope = tr('gui.stats_window', start=window['date_from'], end=window['date_to'],
                   lower=window['start'], upper=window['end'])
        self.range_label.setText(scope.split('\n')[0])
        self.range_label.setToolTip(scope)
        self.updated.setText(tr('gui.dashboard_updated', day=result['day'], time=result['updated']))
        usage = result['usage']
        value, hint = self.metrics['today']
        value.setText(str(result['saved_count']) if result['history_state'] == 'ready' else '—')
        hint.setText(tr('gui.dashboard_saved_only' if result['saving_enabled'] else 'gui.dashboard_saving_off'))
        value, hint = self.metrics['tokens']
        available = usage['state'] == 'ready'
        value.setText(f"{usage['tokens']:,}" if available and (
            usage['known_token_requests'] or not usage['requests']) else '—')
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
        notes = ([tr('gui.stats_unknown_cost_count', count=usage['unknown_cost_requests'])]
                 if usage['unknown_cost_requests'] else [])
        if timings.get('limited'):
            notes.insert(0, tr('gui.stats_limited'))
        if not usage['tracking_enabled']:
            notes.append(tr('gui.dashboard_tracking_off'))
        if result['history_limited'] or usage['limited'] or usage['skipped']:
            notes.append(tr('gui.dashboard_partial'))
        if result['history_state'] != 'ready':
            notes.append(tr('gui.dashboard_history_unavailable'))
        self.note.setText('\n'.join(notes))
        self.note.setVisible(bool(notes))
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
