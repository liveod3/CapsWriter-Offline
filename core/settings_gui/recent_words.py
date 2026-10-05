"""Bounded recent recognition previews and explicit, locally confirmed copy actions."""

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QLayout, QPushButton, QSizePolicy, QVBoxLayout

from core.i18n import tr
from .presentation import text
from .status_page import plain
from .toast import GuiToast


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


class RecentWords(QFrame):
    copied = Signal()

    def __init__(self, request):
        super().__init__()
        self.request = request
        self.pending = None
        self.inflight = False
        self.operation = None
        self.generation = 0
        self.stopped = False
        self.last_entries = None
        self.copy_button = None
        self.copy_toast = None
        self.copied.connect(self.notify_copied)
        self.setObjectName('card')
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(8)
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        box.addWidget(text('dashboard_recent', 'cardTitle'))
        box.addWidget(text('dashboard_copy_hint', 'muted'))
        self.note = plain()
        self.note.hide()
        box.addWidget(self.note)
        self.entries = QVBoxLayout()
        self.entries.setContentsMargins(0, 0, 0, 0)
        self.entries.setSpacing(8)
        box.addLayout(self.entries)
        self.empty = text('dashboard_empty', 'emptyHint')
        box.addWidget(self.empty)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.setInterval(100)
        self.retry.timeout.connect(self.flush)
        self.timer = QTimer(self)
        self.timer.setInterval(30000)
        self.timer.timeout.connect(self.refresh)

    def copy_record(self, reference, button):
        self.copy_button = button
        if self.note.text().startswith(tr('gui.dashboard_failed')):
            self.note.clear()
            self.note.hide()
        self.show_copy_feedback('')
        self.schedule('dashboard_copy', reference)

    def show_copy_feedback(self, message):
        # Keep failures beside the clicked text; pending reads leave the row unchanged.
        for index in range(self.entries.count()):
            button = self.entries.itemAt(index).widget()
            feedback = button.layout().itemAt(0).widget()
            feedback.setText(button.timestamp + (' · ' + message if message and button is self.copy_button else ''))
            button.setAccessibleDescription(message if button is self.copy_button else '')

    def showEvent(self, event):
        super().showEvent(event)
        if not self.stopped:
            self.timer.start()
            self.refresh(force=True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.timer.stop()
        self.dismiss_toast()
        self.retry.stop()
        self.pending = None
        self.generation += 1

    def stop(self):
        self.stopped = True
        self.timer.stop()
        self.dismiss_toast()
        self.retry.stop()
        self.pending = None
        self.generation += 1

    def resume(self):
        self.stopped = False
        if self.isVisible():
            self.timer.start()

    def notify_copied(self):
        if self.stopped or not self.isVisible():
            return
        if self.copy_toast is None:
            self.copy_toast = GuiToast(self.window())
        self.copy_toast.present(tr('gui.dashboard_copied'))

    def dismiss_toast(self):
        if self.copy_toast is not None:
            self.copy_toast.dismiss()

    def refresh(self, *_, force=False):
        if not force and ((self.inflight and self.operation == 'dashboard_copy') or (
                self.pending and self.pending[0] == 'dashboard_copy')):
            return
        self.schedule('history_query', {})

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
                    self.show_copy_feedback('')
                    self.copied.emit()
                else:
                    self.show_data(result)
            self.flush()
        def failed(error):
            self.inflight = False
            if not self.stopped and self.isVisible() and generation == self.generation:
                self.note.setText(tr('gui.dashboard_failed') + error)
                self.note.show()
                if method == 'dashboard_copy':
                    self.show_copy_feedback(tr('gui.dashboard_failed') + error)
            self.flush()
        done.on_error = failed
        if self.request(method, params, done, quiet=True):
            self.inflight = True
            self.operation = method
            self.pending = None
        else:
            self.retry.start()

    def show_data(self, result):
        notes = []
        if result['limited'] or result['skipped']:
            notes.append(tr('gui.dashboard_partial'))
        if not result['saving_enabled']:
            notes.append(tr('gui.dashboard_saving_off'))
        self.note.setText('\n'.join(notes))
        self.note.setVisible(bool(notes))
        result = {**result, 'entries': result['entries'][:5]}
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
            button.timestamp = entry['day'] + '  ' + entry['time']
            row = QVBoxLayout(button)
            row.setContentsMargins(12, 10, 12, 10)
            row.setSpacing(5)
            for caption, style in ((entry['day'] + '  ' + entry['time'], 'muted'), (entry['preview'], 'recordText')):
                label = plain(caption, style)
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                row.addWidget(label)
            button.setAccessibleName(tr('gui.dashboard_copy_entry', text=entry['preview']))
            reference = {key: entry[key] for key in ('day', 'offset', 'length', 'digest')}
            button.clicked.connect(lambda _checked=False, ref=reference, row=button: self.copy_record(ref, row))
            self.entries.addWidget(button)
        self.empty.setVisible(not result['entries'])
