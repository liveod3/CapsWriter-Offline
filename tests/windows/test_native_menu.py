"""原生菜单资源检查，不启动麦克风、不显示菜单或读取用户文本。"""

import ctypes
from ctypes import wintypes as W
import sys
import pytest

if sys.platform != "win32":
    pytest.skip("Windows native menu only", allow_module_level=True)

from pystray import Menu
from core.ui.menu_model import MenuAction
from core.ui.tray_native import NativeMenuIcon, G, U, win32
from unittest.mock import Mock


def test_menu_owner_preserves_default_window_messages_and_tooltip_dispatch():
    icon = NativeMenuIcon("capswriter-menu-owner-test")
    icon._menu_hwnd = icon._create_window(icon._atom)
    try:
        text = ctypes.create_unicode_buffer("Menu render probe")
        # 直接设置原生窗口状态，随后必须仍能通过消息分发读取。
        win32.DefWindowProc(icon._menu_hwnd, 0xC, 0, ctypes.addressof(text))
        assert U.SendMessageW(icon._menu_hwnd, 0xE, 0, 0) == len(text.value)
        icon._attach_menu_window()
        callback = icon._menu_wndproc
        icon._attach_menu_window()
        assert icon._menu_wndproc is callback
        assert icon._menu_hwnd not in icon._HWND_TO_ICON
        assert U.SendMessageW(icon._menu_hwnd, 0xE, 0, 0) == len(text.value)

        icon._select = Mock()
        icon._timer = Mock()
        icon._exit_menu = Mock()
        U.SendMessageW(icon._menu_hwnd, 0x11F, 2, 3)
        U.SendMessageW(icon._menu_hwnd, 0x113, 71, 0)
        U.SendMessageW(icon._menu_hwnd, 0x113, 72, 0)
        U.SendMessageW(icon._menu_hwnd, 0x212, 0, 0)
        icon._select.assert_called_once_with(2, 3)
        icon._timer.assert_called_once_with(71, 0)
        icon._exit_menu.assert_called_once_with(0, 0)
    finally:
        U.DestroyWindow(icon._menu_hwnd)
        icon._unregister_class(icon._atom)


def test_native_menu_has_bitmaps_and_submenu_tooltip_mapping():
    action = MenuAction(
        "Text actions",
        tooltip="Process a transcript.",
        icon="text",
        children=[MenuAction("Translate", lambda: None, "Translate once.", "translate")],
    )
    icon = NativeMenuIcon("capswriter-native-test")
    callbacks = []
    menu = None
    try:
        menu = icon._create_menu(Menu(action.to_item()), callbacks)
        assert menu and len(callbacks) == 2
        U.GetMenuItemInfoW.argtypes = [W.HMENU, W.UINT, W.BOOL, ctypes.POINTER(win32.MENUITEMINFO)]
        item = win32.MENUITEMINFO(
            cbSize=ctypes.sizeof(win32.MENUITEMINFO), fMask=0x80 | win32.MIIM_SUBMENU
        )
        assert U.GetMenuItemInfoW(menu, 0, True, ctypes.byref(item))
        assert item.hbmpItem and item.hSubMenu
        assert icon._tips[1] == "Process a transcript."
        assert icon._tips[2] == "Translate once."
        assert icon._positions[(int(item.hSubMenu), 0)] == 2
        # 检查原生菜单确实存有文字，避免只验证句柄存在。
        U.GetMenuStringW.argtypes = [W.HMENU, W.UINT, W.LPWSTR, ctypes.c_int, W.UINT]
        label = ctypes.create_unicode_buffer(100)
        assert U.GetMenuStringW(menu, 0, label, len(label), 0x400) == len("Text actions")
        assert label.value == "Text actions"
        assert U.GetMenuStringW(item.hSubMenu, 0, label, len(label), 0x400) == len("Translate")
        assert label.value == "Translate"
    finally:
        if menu:
            win32.DestroyMenu(menu)
        for bitmap in icon._bitmaps.values():
            assert G.DeleteObject(bitmap)
        icon._bitmaps.clear()
        icon._unregister_class(icon._atom)
