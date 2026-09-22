# coding: utf-8
"""
Clipboard package.

Provide clipboard operations:
1. Read text with encoding fallbacks.
2. Write text with error handling.
3. Save and restore clipboard text through a context manager.
4. Paste by simulating Ctrl+V.
"""

from .. import logger
from core.client.clipboard.clipboard import (
    safe_paste,
    safe_copy,
    copy_to_clipboard,
    save_and_restore_clipboard,
    paste_text,
    CLIPBOARD_ENCODINGS,
)

__all__ = [
    'logger',
    'safe_paste',
    'safe_copy',
    'copy_to_clipboard',
    'save_and_restore_clipboard',
    'paste_text',
    'CLIPBOARD_ENCODINGS',
]
