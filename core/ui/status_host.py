"""Own the Tk thread used exclusively by recording and processing status overlays."""

from __future__ import annotations

import ctypes
from queue import Empty, Queue
import threading
import tkinter as tk

from core.i18n import Notice
from . import logger


# Preserve the process DPI mode and Tk scaling previously set by the shared UI host.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except (OSError, AttributeError):
    pass


class StatusUIHost:
    """Create one hidden root and run queued overlay callbacks on its owning thread."""

    _instance = None
    _lock = threading.Lock()
    _stopped = False

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        with self._lock:
            if self._initialized:
                return
            self._initialized = True
            self.ui_queue = Queue()
            self._ui_closed = self._stopped
            self.is_running = False
            self.root = None
            self.thread = None
            if self._ui_closed:
                return
            self.thread = threading.Thread(target=self._run, daemon=True, name='StatusUIThread')
            self.thread.start()

    def _run(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.tk.call('tk', 'scaling', 2)
            self.root.protocol('WM_DELETE_WINDOW', self._on_close)
            with self._lock:
                closed = self._ui_closed
            if closed:
                return
            self.is_running = True
            self._process_queue()
            if self.is_running:
                self.root.mainloop()
        except Exception as exc:
            logger.warning(Notice('diagnostic.status_host.failed'), type(exc).__name__)
        finally:
            self._on_close()
            if self.root is not None:
                try:
                    self.root.destroy()
                except tk.TclError:
                    pass
                self.root = None

    def post_ui(self, callback):
        """Retain work before root readiness; reject late submissions after closure."""
        with self._lock:
            if not self._ui_closed:
                self.ui_queue.put(callback)

    @classmethod
    def stop_existing(cls):
        """Do not create a UI thread just to shut down an unused host."""
        with cls._lock:
            cls._stopped = True
            instance = cls._instance
        if instance is not None:
            instance.stop()

    def stop(self):
        """Reject new work immediately; quit/destroy only on the Tk owner."""
        with self._lock:
            if not self._ui_closed:
                self._ui_closed = True
                while True:
                    try:
                        self.ui_queue.get_nowait()
                    except Empty:
                        break
                self.ui_queue.put(lambda _: self._on_close())
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)

    def _on_close(self):
        """Run only on the owning Tk thread, including initialization failure."""
        with self._lock:
            self._ui_closed = True
            self.is_running = False
            while True:
                try:
                    self.ui_queue.get_nowait()
                except Empty:
                    break
        if self.root is not None:
            from .recording_indicator import close_on_owner
            close_on_owner(self.root)
            try:
                self.root.quit()
            except tk.TclError:
                pass

    def _process_queue(self):
        if not self.is_running:
            return
        for _ in range(128):
            if not self.is_running:
                break
            try:
                callback = self.ui_queue.get_nowait()
            except Empty:
                break
            try:
                callback(self.root)
            except Exception as exc:
                logger.warning(Notice('diagnostic.status_host.update_failed'), type(exc).__name__)
        if self.is_running and self.root is not None:
            self.root.after(100, self._process_queue)
