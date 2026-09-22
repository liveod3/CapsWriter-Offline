# coding: utf-8
"""
Clipboard utilities.

Provide clipboard operations:
1. Read text with encoding fallbacks.
2. Write text with error handling.
3. Save and restore clipboard text through a context manager.
4. Paste by simulating Ctrl+V.
"""

from core.i18n import Notice
import asyncio
import platform
from contextlib import contextmanager
import pyclip
from pynput import keyboard
from . import logger


# Supported text encodings.
CLIPBOARD_ENCODINGS = ['utf-8', 'gbk', 'utf-16', 'latin1']


def safe_paste() -> str:
    """
    Read and decode clipboard text.

    Try supported encodings in order.

    Returns:
        Decoded text, or an empty string on failure.
    """
    try:
        clipboard_data = pyclip.paste()

        # Try supported encodings.
        for encoding in CLIPBOARD_ENCODINGS:
            try:
                return clipboard_data.decode(encoding)
            except (UnicodeDecodeError, AttributeError):
                continue

        # Return an empty string if decoding fails.
        logger.debug(Notice('diagnostic.clipboard.clipboard_decoding_failed_encodings_tried', value0=CLIPBOARD_ENCODINGS))
        return ""

    except Exception as e:
        logger.warning(Notice('diagnostic.clipboard.clipboard_read_failed_error'), type(e).__name__)
        return ""


def safe_copy(content: str) -> bool:
    """
    Copy text to the clipboard with error handling.

    Args:
        content: Text to copy.

    Returns:
        Whether the operation succeeded.
    """
    if not content:
        return False

    try:
        pyclip.copy(content)
        logger.debug(Notice('diagnostic.clipboard.clipboard_write_succeeded_chars', value0=len(content)))
        return True
    except Exception as e:
        logger.warning(Notice('diagnostic.clipboard.clipboard_write_failed_error'), type(e).__name__)
        return False


def copy_to_clipboard(content: str):
    """
    Copy text using the legacy API.

    Args:
        content: Text to copy.
    """
    safe_copy(content)


@contextmanager
def save_and_restore_clipboard():
    """
    Save and restore clipboard text.

    Usage:
        with save_and_restore_clipboard():
            # Use the clipboard within this block.
            pyclip.copy("Temporary content")
        # Restore the previous text on exit.
    """
    original = safe_paste()
    try:
        yield
    finally:
        if original:
            pyclip.copy(original)
            logger.debug(Notice('diagnostic.clipboard.clipboard_restored'))


async def paste_text(text: str, restore_clipboard: bool = True):
    """
    Paste text by simulating Ctrl+V.

    Args:
        text: Text to paste.
        restore_clipboard: Restore the previous clipboard text after pasting.
    """
    # Save clipboard text.
    original = ''
    if restore_clipboard:
        try:
            original = safe_paste()
        except:
            pass

    # Copy the output text.
    pyclip.copy(text)
    logger.debug(Notice('diagnostic.clipboard.text_copied_to_clipboard_chars', value0=len(text)))

    # Simulate Ctrl+V through pynput.
    controller = keyboard.Controller()
    if platform.system() == 'Darwin':
        # macOS: Command+V
        with controller.pressed(keyboard.Key.cmd):
            controller.tap('v')
    else:
        # Windows/Linux: Ctrl+V
        with controller.pressed(keyboard.Key.ctrl):
            controller.tap('v')
    
    logger.debug(Notice('diagnostic.clipboard.paste_command_sent_ctrl_v'))

    # Restore clipboard text.
    if restore_clipboard and original:
        await asyncio.sleep(0.1)
        pyclip.copy(original)
        logger.debug(Notice('diagnostic.clipboard.clipboard_restored'))
