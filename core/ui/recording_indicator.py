# coding: utf-8
"""
Dictation status overlay.

Display recording status at the bottom center of the screen.
Use a dark, borderless window with a pulsing red dot without taking focus.
"""

from core.i18n import tr
import ctypes
import ctypes.wintypes
import tkinter as tk
from typing import Optional, Tuple

from . import logger


# ============================================================
# Use the work area of the monitor containing the pointer.
# ============================================================

def _get_active_monitor_workarea() -> Tuple[int, int, int, int]:
    """Return the pointer monitor's work area as (left, top, width, height).
    Fall back to the primary monitor on failure."""
    try:
        # Get pointer coordinates.
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))

        # Find the nearest monitor with MONITOR_DEFAULTTONEAREST.
        hmon = ctypes.windll.user32.MonitorFromPoint(pt, 2)

        # Read monitor information.
        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ('cbSize',    ctypes.c_uint32),
                ('rcMonitor', ctypes.wintypes.RECT),
                ('rcWork',    ctypes.wintypes.RECT),
                ('dwFlags',   ctypes.c_uint32),
            ]
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))

        r = mi.rcWork
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        # Fall back to Tk's primary screen dimensions.
        try:
            import tkinter as _tk
            _r = _tk.Tk()
            _r.withdraw()
            w = _r.winfo_screenwidth()
            h = _r.winfo_screenheight()
            _r.destroy()
            return 0, 0, w, h
        except Exception:
            return 0, 0, 1920, 1080


# ============================================================
# Pulse the red indicator.
# ============================================================

_PULSE_COLORS = [
    '#FF3B30', '#FF4C3F', '#FF5D4E', '#FF6E5D',
    '#FF5D4E', '#FF4C3F', '#FF3B30', '#EE3428',
    '#DD2D20', '#EE3428', '#FF3B30',
]
_PULSE_INTERVAL_MS = 100


class _RecordingIndicator:
    """Show a bottom-center recording overlay without taking focus."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._win: Optional[tk.Toplevel] = None
        self._dot: Optional[tk.Label] = None
        self._pulse_idx = 0
        self._pulse_job = None
        self._hint_win: Optional[tk.Toplevel] = None
        self._hint_job = None
        self._processing_text = ''

    # Tk-thread implementation.

    def _show_impl(self) -> None:
        if self._win and self._win.winfo_exists():
            return

        win = tk.Toplevel(self._root)
        win.overrideredirect(True)          # Remove borders and title bar.
        win.attributes('-topmost', True)
        win.attributes('-alpha', 0.88)
        win.configure(bg='#1C1C1E')

        frame = tk.Frame(win, bg='#1C1C1E', padx=20, pady=10)
        frame.pack()

        dot = tk.Label(
            frame,
            text='●',
            font=('', 11),
            fg='#FF3B30',
            bg='#1C1C1E',
        )
        dot.pack(side='left', padx=(0, 8))

        tk.Label(
            frame,
            text=tr('mic.recording'),
            font=('Microsoft YaHei UI', 10),
            fg='#EBEBF5',
            bg='#1C1C1E',
        ).pack(side='left')

        # Place at the bottom center of the pointer monitor's work area.
        win.update_idletasks()
        ww = win.winfo_reqwidth()
        wh = win.winfo_reqheight()
        mx, my, mw, mh = _get_active_monitor_workarea()
        x = mx + (mw - ww) // 2
        y = my + mh - wh - 80   # Leave 80 px above the work-area bottom.
        win.geometry(f'+{x}+{y}')

        self._win = win
        self._dot = dot
        self._pulse_idx = 0
        self._start_pulse()

        # Reposition existing status above the recording overlay to prevent overlap.
        self._relayout_hint_if_needed()

    def _hide_impl(self) -> None:
        self._stop_pulse()
        if self._win:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None
            self._dot = None

    def _show_hint_impl(self, text: str, duration_ms: int, dot_color: str) -> None:
        # Replace the previous notification so only one status notification is visible.
        if self._hint_job:
            try:
                self._root.after_cancel(self._hint_job)
            except Exception:
                pass
            self._hint_job = None

        if self._hint_win and self._hint_win.winfo_exists():
            try:
                self._hint_win.destroy()
            except tk.TclError:
                pass
            self._hint_win = None

        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes('-topmost', True)
        win.attributes('-alpha', 0.9)
        win.configure(bg='#1C1C1E')

        frame = tk.Frame(win, bg='#1C1C1E', padx=18, pady=9)
        frame.pack()
        mx, my, mw, mh = _get_active_monitor_workarea()

        tk.Label(
            frame,
            text='●',
            font=('', 10),
            fg=dot_color,
            bg='#1C1C1E',
        ).pack(side='left', padx=(0, 8))

        tk.Label(
            frame,
            text=text,
            wraplength=max(80, min(640, mw - 100)),
            justify='left',
            font=('Microsoft YaHei UI', 10),
            fg='#EBEBF5',
            bg='#1C1C1E',
        ).pack(side='left')

        win.update_idletasks()
        ww = win.winfo_reqwidth()
        wh = win.winfo_reqheight()
        mx, my, mw, mh = _get_active_monitor_workarea()
        x = mx + (mw - ww) // 2
        y = my + mh - wh - 120

        # Keep the status notification above an active recording overlay.
        if self._win and self._win.winfo_exists():
            try:
                self._win.update_idletasks()
                rec_y = self._win.winfo_y()
                y = min(y, rec_y - wh - 12)
            except tk.TclError:
                pass

        # Leave a top margin on small screens or at high display scaling.
        y = max(my + 16, y)
        win.geometry(f'+{x}+{y}')

        self._hint_win = win
        if duration_ms:
            self._hint_job = self._root.after(max(300, int(duration_ms)), self._hide_hint_impl)

    def _hide_hint_impl(self) -> None:
        if self._hint_job:
            try:
                self._root.after_cancel(self._hint_job)
            except tk.TclError:
                pass
        if self._hint_win:
            try:
                self._hint_win.destroy()
            except tk.TclError:
                pass
            self._hint_win = None
        self._hint_job = None
        if self._processing_text:
            self._show_hint_impl(self._processing_text, 0, '#7DD3FC')

    def _set_processing_impl(self, text: str) -> None:
        self._processing_text = text
        # Restore ongoing task status after short notices; task cleanup must not dismiss other errors.
        if self._hint_job is None:
            self._hide_hint_impl()

    def _relayout_hint_if_needed(self) -> None:
        """Move the status notification above the recording overlay when needed."""
        if not (self._hint_win and self._hint_win.winfo_exists() and self._win and self._win.winfo_exists()):
            return

        try:
            self._hint_win.update_idletasks()
            self._win.update_idletasks()

            hw = self._hint_win.winfo_reqwidth()
            hh = self._hint_win.winfo_reqheight()
            mx, my, mw, mh = _get_active_monitor_workarea()

            x = mx + (mw - hw) // 2
            y = min(my + mh - hh - 120, self._win.winfo_y() - hh - 12)
            y = max(my + 16, y)
            self._hint_win.geometry(f'+{x}+{y}')
        except tk.TclError:
            pass

    def _start_pulse(self) -> None:
        self._pulse_job = self._root.after(_PULSE_INTERVAL_MS, self._pulse_step)

    def _stop_pulse(self) -> None:
        if self._pulse_job:
            try:
                self._root.after_cancel(self._pulse_job)
            except Exception:
                pass
            self._pulse_job = None

    def _pulse_step(self) -> None:
        if self._dot is None:
            return
        try:
            color = _PULSE_COLORS[self._pulse_idx % len(_PULSE_COLORS)]
            self._dot.configure(fg=color)
            self._pulse_idx += 1
            self._pulse_job = self._root.after(_PULSE_INTERVAL_MS, self._pulse_step)
        except tk.TclError:
            pass


# ============================================================
# Shared singleton.
# ============================================================

_indicator: Optional[_RecordingIndicator] = None


def _post_indicator(action) -> None:
    """Enqueue Tk work without blocking shortcut or asyncio threads on root readiness."""
    from .toast_manager import ToastMessageManager

    def apply(root):
        global _indicator
        if _indicator is None:
            _indicator = _RecordingIndicator(root)
        action(_indicator)

    ToastMessageManager().post_ui(apply)


def show_recording_indicator() -> None:
    """Enqueue showing the recording overlay from any thread."""
    _post_indicator(lambda ind: ind._show_impl())


def hide_recording_indicator() -> None:
    """Enqueue hiding the recording overlay from any thread."""
    _post_indicator(lambda ind: ind._hide_impl())


def show_status_hint(text: str, duration_ms: int = 1600, dot_color: str = '#7DD3FC') -> None:
    """Enqueue a brief status notification."""
    _post_indicator(lambda ind: ind._show_hint_impl(text, duration_ms, dot_color))


def hide_status_hint() -> None:
    """Enqueue hiding the current status notification."""
    _post_indicator(lambda ind: ind._hide_hint_impl())


def set_processing_status(text: str) -> None:
    """Keep task status visible; empty text clears it, and short notices temporarily cover it."""
    _post_indicator(lambda ind: ind._set_processing_impl(text))
