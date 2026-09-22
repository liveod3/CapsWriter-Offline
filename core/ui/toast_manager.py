from __future__ import annotations
"""
Toast message management.

Manage Toast window lifetimes through the ToastMessageManager singleton.
"""

from core.i18n import Notice
import logging
import threading
import tkinter as tk
from queue import Queue, Empty
from dataclasses import dataclass
from typing import Literal, Optional, Callable, Union, List, TYPE_CHECKING
import sys
import os

# Add the project root to sys.path when run directly.
if __name__ == "__main__":
    file_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(file_dir))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from core.ui.toast_text import ToastWindowText
    from core.ui.toast_label import ToastWindowLabel
    from core.ui.toast_constants import (
        QUEUE_POLL_INTERVAL_MS,
        DEFAULT_DURATION_MS,
        DEFAULT_INITIAL_WIDTH,
        TK_SCALING_FACTOR,
    )
    from core.ui.toast_logger import get_toast_logger
else:
    from .toast_text import ToastWindowText
    from .toast_label import ToastWindowLabel
    from .toast_constants import (
        QUEUE_POLL_INTERVAL_MS,
        DEFAULT_DURATION_MS,
        DEFAULT_INITIAL_WIDTH,
        TK_SCALING_FACTOR,
    )
    from .toast_logger import get_toast_logger

# Forward references for type annotations.
if TYPE_CHECKING:
    from .toast_base import ToastWindowBase


# Reuse application logging when available.
logger = get_toast_logger(__name__)


# ============================================================
# Configuration dataclass.
# ============================================================

@dataclass
class ToastMessage:
    """Toast message configuration.

    Attributes:
        text: Message text.
        font_size: Font size in pixels.
        font_family: Font family; empty uses the system default.
        bg: Background color as hex or a color name.
        fg: Foreground text color.
        duration: Display duration in milliseconds.
        initial_width: Screen fraction for values 0-1; pixels for values above 1.
        initial_height: Initial height; 0 calculates it automatically.
        streaming: Enable streaming mode.
        window_type: 'text' or 'label'.
        stop_callback: Callback invoked when closing.
        markdown: Enable Markdown rendering.
    """
    text: str
    font_size: int = 14
    font_family: str = ''
    bg: str = '#075077'
    fg: str = 'white'
    duration: int = DEFAULT_DURATION_MS
    initial_width: Union[float, int] = DEFAULT_INITIAL_WIDTH
    initial_height: int = 0
    streaming: bool = False
    window_type: Literal['text', 'label'] = 'text'
    stop_callback: Optional[Callable[[], None]] = None
    markdown: bool = False
    editable: bool = False  # Allow editing after Markdown rendering.


# ============================================================
# Toast message manager.
# ============================================================

class ToastMessageManager:
    """Manage Toast messages through one shared instance.

    Run Tk's event loop on its own thread and own Toast window lifetimes.

    Features:
        - One singleton and one Tk event loop.
        - A queue for concurrent producers.
        - Active-window tracking for streaming updates.
        - UUID message identities for targeted operations.
    """

    _instance: Optional[ToastMessageManager] = None
    _lock = threading.Lock()

    def __new__(cls) -> ToastMessageManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        # Concurrent first submissions must not observe a partially initialized queue.
        with self._lock:
            if self._initialized:
                return
            self._initialize()

    def _initialize(self) -> None:
        self._initialized = True
        self.message_queue: Queue[ToastMessage] = Queue()
        self.ui_queue: Queue = Queue()
        self._ui_closed = False
        self.is_running = False
        self.active_windows: List = []  # Resolve runtime types without circular imports.
        self.root: Optional[tk.Tk] = None

        # Start Tk on its owning thread.
        self.manager_thread = threading.Thread(
            target=self._run_manager,
            daemon=True,
            name="ToastManagerThread"
        )
        self.manager_thread.start()

    def _run_manager(self) -> None:
        """Run the Tk event loop on its owning thread."""
        # Create a hidden root window.
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.tk.call('tk', 'scaling', TK_SCALING_FACTOR)

        # Register window-close behavior.
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Start queue processing.
        self.is_running = True
        self._process_queue()

        # Enter the Tk event loop.
        self.root.mainloop()

    def _on_close(self) -> None:
        """Close all windows and exit."""
        self.is_running = False
        self._ui_closed = True

        for window in self.active_windows[:]:
            try:
                window.window.destroy()
            except tk.TclError:
                pass

        self.active_windows.clear()

        if self.root:
            self.root.quit()

    def _process_queue(self) -> None:
        """Process queued messages."""
        if not self.is_running:
            return
        try:
            # Create/update windows only on the Tk thread; bound each batch to keep it responsive.
            for _ in range(128):
                try:
                    callback = self.ui_queue.get_nowait()
                except Empty:
                    break
                try:
                    callback(self.root)
                except Exception as exc:
                    logger.warning(Notice('diagnostic.toast_manager.status_ui_update_failed'), type(exc).__name__)
            if not self.message_queue.empty():
                msg = self.message_queue.get_nowait()
                msg_id = getattr(msg, '_id', 'unknown')

                # Select the window class by window_type.
                WindowClass = ToastWindowLabel if msg.window_type == 'label' else ToastWindowText

                toast_window = WindowClass(
                    self.root,
                    msg.text,
                    msg.font_size,
                    msg.font_family,
                    msg.bg,
                    msg.fg,
                    msg.duration,
                    msg.initial_width,
                    msg.initial_height,
                    streaming=msg.streaming,
                    stop_callback=msg.stop_callback,
                    markdown=msg.markdown,
                    editable=msg.editable
                )

                # Map the message ID to its window.
                toast_window._msg_id = msg_id
                self.active_windows.append(toast_window)

                # Register the destruction callback.
                toast_window.window.bind(
                    '<Destroy>',
                    lambda _, w=toast_window: self._remove_window(w)
                )

            # Remove destroyed windows.
            self.active_windows = [
                w for w in self.active_windows
                if self._window_exists(w)
            ]

        except Exception as e:
            logger.warning(Notice('diagnostic.toast_manager.message_queue_processing_failed', value0=e))

        # Schedule the next queue pass.
        if self.is_running and self.root:
            self.root.after(QUEUE_POLL_INTERVAL_MS, self._process_queue)

    def post_ui(self, callback) -> None:
        """Enqueue without waiting; retain work before root readiness and discard after closure."""
        if not self._ui_closed:
            self.ui_queue.put(callback)

    def _window_exists(self, window) -> bool:
        """Return whether the window exists."""
        try:
            return window.window.winfo_exists()
        except tk.TclError:
            return False

    def _remove_window(self, window) -> None:
        """Remove a window from the active set."""
        if window in self.active_windows:
            self.active_windows.remove(window)

    def add_message(self, msg: ToastMessage) -> Optional[str]:
        """Enqueue a ToastMessage.

        Args:
            msg: Message configuration.

        Returns:
            Unique message ID for updates, completion, and closure.
        """
        import uuid
        msg_id = str(uuid.uuid4())
        msg._id = msg_id  # Assign a unique identifier.
        self.message_queue.put(msg)
        return msg_id

    def update_toast(self, msg_id: str, new_text: str) -> None:
        """Update the Toast identified by msg_id.

        Args:
            msg_id: Unique message identifier.
            new_text: Complete replacement text.
        """
        for window in self.active_windows:
            if getattr(window, '_msg_id', None) == msg_id:
                window.update_text(new_text)
                return
        logger.warning(Notice('diagnostic.toast_manager.message_id_not_found', value0=msg_id[:8]))

    def finish_toast(self, msg_id: str) -> None:
        """Finish streaming for the Toast identified by msg_id.

        Args:
            msg_id: Unique message identifier.
        """
        for window in self.active_windows:
            if getattr(window, '_msg_id', None) == msg_id:
                if window.streaming:
                    window.finish()
                return
        logger.warning(Notice('diagnostic.toast_manager.message_id_not_found', value0=msg_id[:8]))

    def close_toast(self, msg_id: str) -> None:
        """Close the Toast identified by msg_id.

        Args:
            msg_id: Unique message identifier.
        """
        for window in self.active_windows[:]:
            if getattr(window, '_msg_id', None) == msg_id:
                try:
                    window.window.destroy()
                    self.active_windows.remove(window)
                except (tk.TclError, ValueError):
                    pass
                return
        logger.warning(Notice('diagnostic.toast_manager.message_id_not_found', value0=msg_id[:8]))

    async def wait_for_window(self, msg_id: str, timeout: float = 1.0) -> Optional[ToastWindowBase]:
        """Wait asynchronously for the requested window to be created.

        Args:
            msg_id: Unique message identifier.
            timeout: Deadline in seconds.

        Returns:
            Window instance, or None on timeout.
        """
        import asyncio
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            for window in self.active_windows:
                if getattr(window, '_msg_id', None) == msg_id:
                    return window
            await asyncio.sleep(0.01)  # Poll every 10 ms.
        logger.warning(Notice('diagnostic.toast_manager.timed_out_waiting_for_window', value0=msg_id[:8]))
        return None
