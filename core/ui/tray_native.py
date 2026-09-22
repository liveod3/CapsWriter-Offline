"""Windows 原生菜单扩展，隔离 pystray 0.19.x 的后端接口。

保持系统菜单绘制和键盘交互；仅添加位图和不抢焦点的跟踪 Tooltip。
"""

from __future__ import annotations

from core.i18n import Notice
import ctypes as C
from ctypes import wintypes as W

from pystray._win32 import Icon
from pystray._util import win32
from .menu_icons import menu_icon

U = C.WinDLL("user32", use_last_error=True)
G = C.WinDLL("gdi32", use_last_error=True)
LRESULT = C.c_ssize_t
UINT_PTR = C.c_size_t


class TOOLINFO(C.Structure):
    _fields_ = [
        ("cbSize", W.UINT),
        ("uFlags", W.UINT),
        ("hwnd", W.HWND),
        ("uId", UINT_PTR),
        ("rect", W.RECT),
        ("hinst", W.HINSTANCE),
        ("lpszText", W.LPWSTR),
        ("lParam", W.LPARAM),
        ("lpReserved", C.c_void_p),
    ]


class BITMAPINFOHEADER(C.Structure):
    _fields_ = [
        ("biSize", W.DWORD),
        ("biWidth", W.LONG),
        ("biHeight", W.LONG),
        ("biPlanes", W.WORD),
        ("biBitCount", W.WORD),
        ("biCompression", W.DWORD),
        ("biSizeImage", W.DWORD),
        ("biXPelsPerMeter", W.LONG),
        ("biYPelsPerMeter", W.LONG),
        ("biClrUsed", W.DWORD),
        ("biClrImportant", W.DWORD),
    ]


class MONITORINFO(C.Structure):
    _fields_ = [
        ("cbSize", W.DWORD),
        ("rcMonitor", W.RECT),
        ("rcWork", W.RECT),
        ("dwFlags", W.DWORD),
    ]


U.CreateWindowExW.argtypes = [
    W.DWORD,
    W.LPCWSTR,
    W.LPCWSTR,
    W.DWORD,
    C.c_int,
    C.c_int,
    C.c_int,
    C.c_int,
    W.HWND,
    W.HMENU,
    W.HINSTANCE,
    C.c_void_p,
]
U.CreateWindowExW.restype = W.HWND
U.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
U.SendMessageW.restype = LRESULT
U.SetWindowLongPtrW.argtypes = [W.HWND, C.c_int, LRESULT]
U.SetWindowLongPtrW.restype = LRESULT
U.CallWindowProcW.argtypes = [C.c_void_p, W.HWND, W.UINT, W.WPARAM, W.LPARAM]
U.CallWindowProcW.restype = LRESULT
U.SetTimer.argtypes = [W.HWND, UINT_PTR, W.UINT, C.c_void_p]
U.SetTimer.restype = UINT_PTR
U.KillTimer.argtypes = [W.HWND, UINT_PTR]
U.DestroyWindow.argtypes = [W.HWND]
U.GetMenuItemRect.argtypes = [W.HWND, W.HMENU, W.UINT, C.POINTER(W.RECT)]
U.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
U.MonitorFromPoint.argtypes = [W.POINT, W.DWORD]
U.MonitorFromPoint.restype = W.HMONITOR
U.GetMonitorInfoW.argtypes = [W.HMONITOR, C.POINTER(MONITORINFO)]
G.CreateDIBSection.argtypes = [
    W.HDC,
    C.POINTER(BITMAPINFOHEADER),
    W.UINT,
    C.POINTER(C.c_void_p),
    W.HANDLE,
    W.DWORD,
]
G.CreateDIBSection.restype = W.HBITMAP
G.DeleteObject.argtypes = [W.HANDLE]


def make_bitmap(name, size):
    image = menu_icon(name, size)
    # 32 位 DIB 使用预乘 BGRA，透明边缘不会出现黑边。
    pixels = bytearray()
    rgba = image.tobytes()
    for offset in range(0, len(rgba), 4):
        r, g, b, a = rgba[offset : offset + 4]
        pixels.extend((b * a // 255, g * a // 255, r * a // 255, a))
    header = BITMAPINFOHEADER(
        C.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, len(pixels), 0, 0, 0, 0
    )
    pointer = C.c_void_p()
    bitmap = G.CreateDIBSection(None, C.byref(header), 0, C.byref(pointer), None, 0)
    if not bitmap:
        raise OSError(Notice('validation.tray_native.cannot_allocate_menu_bitmap'))
    C.memmove(pointer, bytes(pixels), len(pixels))
    return bitmap


class NativeMenuIcon(Icon):
    def __init__(self, *args, **kwargs):
        self._bitmaps = {}
        self._tips = {}
        self._positions = {}
        self._tooltip = None
        self._selected = None
        self._size = 20
        self._menu_wndproc = None
        self._original_menu_wndproc = None
        super().__init__(*args, **kwargs)

    def _attach_menu_window(self):
        """仅拦截菜单提示消息，其余消息继续走系统窗口过程。"""
        if self._menu_wndproc is not None:
            return

        @win32.WNDPROC
        def menu_wndproc(hwnd, message, wparam, lparam):
            # pystray 的托盘 dispatcher 会对未知消息返回 0，不能用于菜单
            # 所有者：Windows 的默认菜单绘制也依赖该窗口处理系统消息。
            try:
                if message == 0x11F:  # WM_MENUSELECT
                    self._select(wparam, lparam)
                elif message == 0x113 and wparam == 71:  # 自己创建的 Tooltip 定时器
                    self._timer(wparam, lparam)
                    return 0
                elif message == 0x212:  # WM_EXITMENULOOP
                    self._exit_menu(wparam, lparam)
            except Exception:
                self._log.exception("Menu tooltip message failed")
            return U.CallWindowProcW(self._original_menu_wndproc, hwnd, message, wparam, lparam)

        C.set_last_error(0)
        previous = U.SetWindowLongPtrW(self._menu_hwnd, -4, C.cast(menu_wndproc, C.c_void_p).value)
        if not previous:
            raise C.WinError(C.get_last_error())
        self._original_menu_wndproc = previous
        # ctypes 回调必须活到窗口销毁，不能只保留一个临时函数指针。
        self._menu_wndproc = menu_wndproc

    def _create_menu(self, descriptors, callbacks):
        if not descriptors:
            return None
        menu = win32.CreatePopupMenu()
        for index, descriptor in enumerate(descriptors):
            callbacks.append(self._handler(descriptor))
            identifier = len(callbacks)
            info = self._create_menu_item(descriptor, callbacks)
            name = getattr(descriptor, "caps_icon", "")
            if callable(name):
                name = name(descriptor)
            if name:
                key = (name, self._size)
                if key not in self._bitmaps:
                    self._bitmaps[key] = make_bitmap(name, self._size)
                info.fMask |= 0x80
                info.hbmpItem = self._bitmaps[key]
            win32.InsertMenuItem(menu, index, True, C.byref(info))
            tooltip = getattr(descriptor, "caps_tooltip", "")
            self._tips[identifier] = tooltip(descriptor) if callable(tooltip) else tooltip
            self._positions[(int(menu), index)] = identifier
        return menu

    def _on_notify(self, wparam, lparam):
        if lparam == win32.WM_RBUTTONUP:
            self._attach_menu_window()
            point = W.POINT()
            win32.GetCursorPos(C.byref(point))
            try:
                monitor = U.MonitorFromPoint(point, 2)
                x, y = W.UINT(), W.UINT()
                shcore = C.WinDLL("shcore")
                shcore.GetDpiForMonitor.argtypes = [
                    W.HMONITOR,
                    C.c_int,
                    C.POINTER(W.UINT),
                    C.POINTER(W.UINT),
                ]
                if shcore.GetDpiForMonitor(monitor, 0, C.byref(x), C.byref(y)) == 0:
                    self._size = max(16, round(20 * x.value / 96))
            except OSError:
                pass
            self._tips.clear()
            self._positions.clear()
            self._update_menu()
            try:
                super()._on_notify(wparam, lparam)
            finally:
                self._exit_menu(0, 0)
        else:
            super()._on_notify(wparam, lparam)

    def _hide_tip(self):
        if self._menu_hwnd:
            U.KillTimer(self._menu_hwnd, 71)
        if self._tooltip:
            U.SendMessageW(self._tooltip, 0x411, 0, C.addressof(self._tool))

    def _select(self, wparam, lparam):
        self._hide_tip()
        self._selected = None
        if not lparam or (wparam >> 16) == 0xFFFF:
            return
        flags = (wparam >> 16) & 0xFFFF
        value = wparam & 0xFFFF
        identifier = self._positions.get((int(lparam), value)) if flags & 0x10 else value
        if not identifier or not self._tips.get(identifier):
            return
        position = next(
            (
                index
                for (menu, index), ident in self._positions.items()
                if menu == int(lparam) and ident == identifier
            ),
            None,
        )
        if position is not None:
            self._selected = (int(lparam), position, self._tips[identifier])
            U.SetTimer(self._menu_hwnd, 71, 500, None)

    def _timer(self, wparam, lparam):
        if wparam != 71 or not self._selected:
            return
        U.KillTimer(self._menu_hwnd, 71)
        menu, index, text = self._selected
        rect = W.RECT()
        if not U.GetMenuItemRect(self._menu_hwnd, menu, index, C.byref(rect)):
            return
        if not self._tooltip:
            C.WinDLL("comctl32").InitCommonControls()
            self._tooltip = U.CreateWindowExW(
                0x08000008,
                "tooltips_class32",
                None,
                0x80000003,
                0,
                0,
                0,
                0,
                self._menu_hwnd,
                None,
                None,
                None,
            )
            if not self._tooltip:
                return
            self._tool = TOOLINFO()
            self._tool.cbSize = C.sizeof(TOOLINFO)
            self._tool.hwnd = self._menu_hwnd
            self._tool.uId = 1
            self._tool.uFlags = 0x20 | 0x80
            self._tip_text = C.create_unicode_buffer(text)
            self._tool.lpszText = C.cast(self._tip_text, W.LPWSTR)
            U.SendMessageW(self._tooltip, 0x432, 0, C.addressof(self._tool))
        self._tip_text = C.create_unicode_buffer(text)
        self._tool.lpszText = C.cast(self._tip_text, W.LPWSTR)
        U.SendMessageW(self._tooltip, 0x439, 0, C.addressof(self._tool))
        U.SendMessageW(self._tooltip, 0x418, 0, round(320 * self._size / 20))
        x, y = rect.right + 8, rect.top

        def position(px, py):
            U.SendMessageW(self._tooltip, 0x412, 0, (px & 65535) | ((py & 65535) << 16))

        position(x, y)
        U.SendMessageW(self._tooltip, 0x411, 1, C.addressof(self._tool))
        bounds = W.RECT()
        info = MONITORINFO()
        info.cbSize = C.sizeof(info)
        monitor = U.MonitorFromPoint(W.POINT(rect.left, rect.top), 2)
        if U.GetWindowRect(self._tooltip, C.byref(bounds)) and U.GetMonitorInfoW(
            monitor, C.byref(info)
        ):
            width, height = bounds.right - bounds.left, bounds.bottom - bounds.top
            if x + width > info.rcWork.right:
                x = rect.left - width - 8
            position(
                max(info.rcWork.left, x), max(info.rcWork.top, min(y, info.rcWork.bottom - height))
            )

    def _exit_menu(self, wparam, lparam):
        self._hide_tip()
        self._selected = None

    def _mainloop(self):
        try:
            super()._mainloop()
        finally:
            # 父类已销毁菜单窗口，此时才可以释放窗口过程回调。
            self._menu_wndproc = None
            self._original_menu_wndproc = None
            if self._tooltip:
                U.DestroyWindow(self._tooltip)
                self._tooltip = None
            for bitmap in self._bitmaps.values():
                G.DeleteObject(bitmap)
            self._bitmaps.clear()
