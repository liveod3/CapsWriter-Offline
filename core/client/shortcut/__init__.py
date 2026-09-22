# coding: utf-8
"""
Shortcut package.

Manage keyboard and mouse shortcuts through ShortcutManager.
"""

from .. import logger
from core.client.shortcut.shortcut_config import Shortcut, CommonShortcuts
from core.client.shortcut.shortcut_manager import ShortcutManager

__all__ = [
    'logger',
    'Shortcut',
    'CommonShortcuts',
    'ShortcutManager',
]
