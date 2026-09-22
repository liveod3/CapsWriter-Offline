# coding: utf-8
"""
听写状态指示浮窗

在屏幕底部中央显示极简的录音状态提示。
样式：深色背景，脉冲红点，无边框，不抢焦点。
"""

from core.i18n import tr
import ctypes
import ctypes.wintypes
import tkinter as tk
from typing import Optional, Tuple

from . import logger


# ============================================================
# 多显示器支持：获取光标所在显示器工作区
# ============================================================

def _get_active_monitor_workarea() -> Tuple[int, int, int, int]:
    """返回当前光标所在显示器工作区 (left, top, width, height)。
    失败时回退到主显示器。"""
    try:
        # 获取光标位置
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))

        # 从光标位置获取显示器句柄（MONITOR_DEFAULTTONEAREST）
        hmon = ctypes.windll.user32.MonitorFromPoint(pt, 2)

        # 获取显示器信息
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
        # 回退：tkinter 主显示器
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
# 脉冲动画（红色呼吸效果）
# ============================================================

_PULSE_COLORS = [
    '#FF3B30', '#FF4C3F', '#FF5D4E', '#FF6E5D',
    '#FF5D4E', '#FF4C3F', '#FF3B30', '#EE3428',
    '#DD2D20', '#EE3428', '#FF3B30',
]
_PULSE_INTERVAL_MS = 100


class _RecordingIndicator:
    """底部中央录音状态浮窗（不抢焦点）"""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._win: Optional[tk.Toplevel] = None
        self._dot: Optional[tk.Label] = None
        self._pulse_idx = 0
        self._pulse_job = None
        self._hint_win: Optional[tk.Toplevel] = None
        self._hint_job = None
        self._processing_text = ''

    # ── Tk 线程内部实现 ───────────────────────────────────────

    def _show_impl(self) -> None:
        if self._win and self._win.winfo_exists():
            return

        win = tk.Toplevel(self._root)
        win.overrideredirect(True)          # 无边框/标题栏
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

        # 定位：光标所在显示器的工作区底部中央
        win.update_idletasks()
        ww = win.winfo_reqwidth()
        wh = win.winfo_reqheight()
        mx, my, mw, mh = _get_active_monitor_workarea()
        x = mx + (mw - ww) // 2
        y = my + mh - wh - 80   # 工作区底部上方 80px
        win.geometry(f'+{x}+{y}')

        self._win = win
        self._dot = dot
        self._pulse_idx = 0
        self._start_pulse()

        # 若状态提示已显示，录音浮窗出现后将提示重新布局到其上方，避免重叠。
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
        # 覆盖旧提示，保证屏幕上最多只有一个状态提示
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

        # 如果录音浮窗正在显示，将提示放到录音浮窗上方，保证视觉不重叠。
        if self._win and self._win.winfo_exists():
            try:
                self._win.update_idletasks()
                rec_y = self._win.winfo_y()
                y = min(y, rec_y - wh - 12)
            except tk.TclError:
                pass

        # 给顶部留出最小边距，避免在小屏幕/高缩放下顶到屏幕边缘。
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
        # 短通知结束后恢复仍在进行的任务；清理任务不撤掉别的错误通知。
        if self._hint_job is None:
            self._hide_hint_impl()

    def _relayout_hint_if_needed(self) -> None:
        """录音浮窗显示后，必要时将提示浮窗重新排到上方。"""
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
# 全局单例
# ============================================================

_indicator: Optional[_RecordingIndicator] = None


def _post_indicator(action) -> None:
    """投递到现有 Tk 队列，不在快捷键或 asyncio 线程等待 root。"""
    from .toast_manager import ToastMessageManager

    def apply(root):
        global _indicator
        if _indicator is None:
            _indicator = _RecordingIndicator(root)
        action(_indicator)

    ToastMessageManager().post_ui(apply)


def show_recording_indicator() -> None:
    """显示录音指示浮窗（线程安全，可从任意线程调用）"""
    _post_indicator(lambda ind: ind._show_impl())


def hide_recording_indicator() -> None:
    """隐藏录音指示浮窗（线程安全，可从任意线程调用）"""
    _post_indicator(lambda ind: ind._hide_impl())


def show_status_hint(text: str, duration_ms: int = 1600, dot_color: str = '#7DD3FC') -> None:
    """显示短暂状态提示浮窗（线程安全）。"""
    _post_indicator(lambda ind: ind._show_hint_impl(text, duration_ms, dot_color))


def hide_status_hint() -> None:
    """隐藏当前状态提示浮窗（线程安全）。"""
    _post_indicator(lambda ind: ind._hide_hint_impl())


def set_processing_status(text: str) -> None:
    """持续显示当前处理阶段；空字符串结束，短通知消失后自动恢复。"""
    _post_indicator(lambda ind: ind._set_processing_impl(text))
