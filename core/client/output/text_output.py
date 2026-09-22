# coding: utf-8
"""
Text output module.

Use TextOutput to insert recognition results into the active window.
"""

from __future__ import annotations

from core.i18n import Notice

import asyncio
import platform
from typing import Optional
import re

import keyboard
import pyclip
from pynput import keyboard as pynput_keyboard

from config_client import ClientConfig as Config
from core.tools.window_detector import get_active_window_info
from . import logger


# Count CJK characters, English words, and number sequences as semantic units.
# Each CJK character counts once; contiguous Latin letters or digits form one unit.
_SEMANTIC_UNIT_RE = re.compile(
    r'[一-鿿㐀-䶿豈-﫿]'
    r'|[a-zA-Z]+'
    r'|\d+'
)


def count_semantic_units(text: str) -> int:
    """Count semantic units: one per CJK character, English word, or number."""
    return len(_SEMANTIC_UNIT_RE.findall(text))



class TextOutput:
    """
    Text output handler.
    
    Support simulated typing and clipboard paste.
    """
    
    @staticmethod
    def strip_punc(text: str) -> str:
        """
        Remove the final trailing punctuation mark.

        Remove it at or below trash_punc_thresh semantic units;
        retain punctuation for longer sentences.
        Applications in trash_punc_apps always remove it regardless of length.

        Args:
            text: Original text.

        Returns:
            Processed text.
        """
        if not text or not Config.trash_punc:
            return text

        # Check applications that always strip trailing punctuation.
        force_strip = False
        if Config.trash_punc_apps:
            process_name = get_active_window_info().get('process_name', '').lower()
            if any(app.lower() == process_name for app in Config.trash_punc_apps):
                force_strip = True

        if not force_strip and Config.trash_punc_thresh > 0 and count_semantic_units(text) > Config.trash_punc_thresh:
            return text

        clean_text = re.sub(f"(?<=.)[{Config.trash_punc}]$", "", text)
        return clean_text
    
    async def output(self, text: str, paste: Optional[bool] = None) -> None:
        """
        Output recognition results.
        
        Choose simulated typing or paste from the configuration.
        
        Args:
            text: Text to output.
            paste: Override paste mode; None uses the configured value.
        """
        if not text:
            return
        
        # Select the output method.
        if paste is None:
            paste = Config.paste
        
        if paste:
            await self._paste_text(text)
        else:
            self._type_text(text)
    
    async def _paste_text(self, text: str) -> None:
        """
        Output text through clipboard paste.
        
        Args:
            text: Text to paste.
        """
        logger.debug(Notice('diagnostic.text_output.outputting_text_by_paste_chars', value0=len(text)))
        
        # Save clipboard contents.
        try:
            temp = pyclip.paste().decode('utf-8')
        except Exception:
            temp = ''
        
        # Copy the result.
        pyclip.copy(text)
        
        # Simulate Ctrl+V through pynput.
        controller = pynput_keyboard.Controller()
        if platform.system() == 'Darwin':
            # macOS: Command+V
            with controller.pressed(pynput_keyboard.Key.cmd):
                controller.tap('v')
        else:
            # Windows/Linux: Ctrl+V
            with controller.pressed(pynput_keyboard.Key.ctrl):
                controller.tap('v')
        
        logger.debug(Notice('diagnostic.clipboard.paste_command_sent_ctrl_v'))
        
        # Restore clipboard text.
        if Config.restore_clip:
            await asyncio.sleep(0.1)
            pyclip.copy(temp)
            logger.debug(Notice('diagnostic.clipboard.clipboard_restored'))
    
    def _type_text(self, text: str) -> None:
        """
        Output text through simulated typing.

        Use keyboard.write instead of pynput.keyboard.Controller.type()
        to avoid conflicts with Chinese input methods.

        Args:
            text: Text to output.
        """
        logger.debug(Notice('diagnostic.text_output.outputting_text_by_typing_chars', value0=len(text)))
        keyboard.write(text)
