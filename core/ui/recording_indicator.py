# coding: utf-8
"""
听写状态指示浮窗

在屏幕底部中央显示极简的录音状态提示。
样式：深色背景，脉冲红点，无边框，不抢焦点。
"""
import ctypes
import ctypes.wintypes
import threading
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

    # ── 公共接口（线程安全，调度到 Tk 线程）──────────────────

    def show(self) -> None:
        self._root.after(0, self._show_impl)

    def hide(self) -> None:
        self._root.after(0, self._hide_impl)

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
            text='听写中',
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

    def _hide_impl(self) -> None:
        self._stop_pulse()
        if self._win:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None
            self._dot = None

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
_lock = threading.Lock()


def _get_indicator() -> Optional[_RecordingIndicator]:
    """获取或创建指示器实例（等待 ToastManager root 就绪）"""
    global _indicator
    if _indicator is not None:
        return _indicator

    import time
    from .toast_manager import ToastMessageManager
    mgr = ToastMessageManager()
    for _ in range(50):
        if mgr.root is not None:
            break
        time.sleep(0.1)

    if mgr.root is None:
        logger.warning('RecordingIndicator: Tk root 未就绪，跳过显示')
        return None

    with _lock:
        if _indicator is None:
            _indicator = _RecordingIndicator(mgr.root)
    return _indicator


def show_recording_indicator() -> None:
    """显示录音指示浮窗（线程安全，可从任意线程调用）"""
    ind = _get_indicator()
    if ind:
        ind.show()


def hide_recording_indicator() -> None:
    """隐藏录音指示浮窗（线程安全，可从任意线程调用）"""
    with _lock:
        ind = _indicator
    if ind:
        ind.hide()
