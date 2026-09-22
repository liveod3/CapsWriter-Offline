# coding: utf-8
"""
Global hotkey package.

Listen for global shortcuts through pynput GlobalHotKeys.
"""

from .. import logger
from core.client.global_hotkey.global_hotkey import (
    GlobalHotkeyManager,
    get_global_hotkey_manager,
)

__all__ = [
    'logger',
    'GlobalHotkeyManager',
    'get_global_hotkey_manager',
]
