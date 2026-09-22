"""Inspect native menu resources without microphones, visible menus, or user text."""

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
from core.i18n import lazy, set_language, tr


def test_menu_owner_preserves_default_window_messages_and_tooltip_dispatch():
    icon = NativeMenuIcon("capswriter-menu-owner-test")
    icon._menu_hwnd = icon._create_window(icon._atom)
    try:
        text = ctypes.create_unicode_buffer("Menu render probe")
        # Set native state directly, then verify message dispatch can still read it.
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
        "LLM actions",
        tooltip="Control LLM actions.",
        icon="text",
        children=[MenuAction(
            "Translation: Currently on", lambda: None, "Control translation.", "translate",
            checked=lambda _item: True,
        )],
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
        assert icon._tips[1] == "Control LLM actions."
        assert icon._tips[2] == "Control translation."
        assert icon._positions[(int(item.hSubMenu), 0)] == 2
        child = win32.MENUITEMINFO(
            cbSize=ctypes.sizeof(win32.MENUITEMINFO), fMask=win32.MIIM_STATE | win32.MIIM_FTYPE
        )
        assert U.GetMenuItemInfoW(item.hSubMenu, 0, True, ctypes.byref(child))
        assert child.fState & win32.MFS_CHECKED
        assert not child.fType & win32.MFT_RADIOCHECK
        # Verify menu text as well as handle existence.
        U.GetMenuStringW.argtypes = [W.HMENU, W.UINT, W.LPWSTR, ctypes.c_int, W.UINT]
        label = ctypes.create_unicode_buffer(100)
        assert U.GetMenuStringW(menu, 0, label, len(label), 0x400) == len("LLM actions")
        assert label.value == "LLM actions"
        assert U.GetMenuStringW(item.hSubMenu, 0, label, len(label), 0x400) == len("Translation: Currently on")
        assert label.value == "Translation: Currently on"
    finally:
        if menu:
            win32.DestroyMenu(menu)
        for bitmap in icon._bitmaps.values():
            assert G.DeleteObject(bitmap)
        icon._bitmaps.clear()
        icon._unregister_class(icon._atom)


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
@pytest.mark.parametrize('size', [20, 30, 40])
def test_localized_native_labels_and_tooltips(language, size):
    """Exercise native resource creation at 100/150/200 percent icon sizes."""
    action = MenuAction(lazy('tray.settings'), tooltip=lazy('tray.settings.tip'), icon='settings',
                        children=[MenuAction(lazy('language.title'), lambda: None,
                                             lazy('language.tip'), 'translate')])
    set_language(language)
    icon = NativeMenuIcon('capswriter-localized-native-test')
    icon._size = size
    menu = None
    try:
        menu = icon._create_menu(Menu(action.to_item()), [])
        U.GetMenuStringW.argtypes = [W.HMENU, W.UINT, W.LPWSTR, ctypes.c_int, W.UINT]
        U.GetMenuStringW.restype = ctypes.c_int
        label = ctypes.create_unicode_buffer(256)
        assert U.GetMenuStringW(menu, 0, label, len(label), 0x400) > 0
        assert label.value == tr('tray.settings')
        assert icon._tips[1] == tr('tray.settings.tip')
        assert icon._tips[2] == tr('language.tip')
        assert all(key[1] == size for key in icon._bitmaps)
    finally:
        if menu:
            win32.DestroyMenu(menu)
        for bitmap in icon._bitmaps.values():
            assert G.DeleteObject(bitmap)
        icon._bitmaps.clear()
        icon._unregister_class(icon._atom)
