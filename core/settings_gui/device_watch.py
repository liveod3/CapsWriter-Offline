"""Coalesce native device changes and apply them only to a visible settings page."""

from collections import deque
import os
import time

from PySide6.QtCore import QTimer


def subscribe(notify):
    if os.name != 'nt':
        return None
    try:
        from .endpoint_notifications import EndpointNotifications
        return EndpointNotifications(notify)
    except Exception:
        return None


class DeviceWatch:
    def __init__(self, parent, visible, refresh):
        self.visible = visible
        self.refresh = refresh
        self.events = deque(maxlen=1)
        self.native = None
        self.started = False
        self.dirty = False
        self.due = 0.0
        self.fallback_due = 0.0
        self.timer = QTimer(parent)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)

    def notify(self):
        # Native callbacks only publish a bounded marker, never touch Qt or COM.
        if self.started:
            self.events.append(True)

    def start(self):
        if not self.started:
            self.started = True
            if self.native is None:
                self.native = subscribe(self.notify)
            self.fallback_due = time.monotonic() + 15
            self.timer.start()

    def tick(self):
        if not self.started:
            return
        now = time.monotonic()
        if self.events:
            self.events.popleft()
            self.dirty, self.due = True, now + 0.5
        if self.native is None and now >= self.fallback_due:
            self.dirty = True
            self.fallback_due = now + 15
        if self.dirty and now >= self.due and self.visible():
            self.dirty = False
            self.refresh()

    def stop(self):
        self.started = False
        self.timer.stop()
        self.dirty = False
        self.events.clear()
        if self.native is not None:
            try:
                self.native.close()
            except Exception:
                # Retain callback ownership if Windows rejects unregistration;
                # the stopped watcher ignores late notifications. Retry on cleanup.
                return
            self.native = None
