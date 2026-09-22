# coding: utf-8
"""
Text formatter.

Postprocess raw recognition text with punctuation and inverse text normalization.
"""

from core.i18n import Notice

from core.tools.chinese_itn import chinese_to_num
from core.tools.format_tools import adjust_space
from config_server import ServerConfig as Config
from . import logger


class TextFormatter:
    """
    Text formatter.
    
    Expose one interface for the formatting tools.
    """
    def __init__(self, punc_model=None):
        """
        Initialize the formatter.
        
        Args:
            punc_model: Optional punctuation model.
        """
        self.punc_model = punc_model

    def format(self, text: str, *, formatting=None) -> str:
        """
        Apply the configured formatting rules.

        Processing order:
        1. Restore punctuation through punc_model.punctuate.
        2. Convert Chinese number words to Arabic numerals.
        3. Adjust spaces between Chinese, English, and numbers.
        
        Args:
            text: Input text.

        Returns:
            Formatted text.
        """
        if not text:
            return ""

        # 1. Restore punctuation.
        if self.punc_model:
            try:
                # Use the shared PuncEngine interface.
                text = self.punc_model.punctuate(text)
            except Exception as e:
                logger.warning(Notice('diagnostic.text_formatter.punctuation_failed_error'), type(e).__name__)

        # 2. Convert Chinese number words to Arabic numerals.
        format_num, format_spell = formatting if formatting is not None else (Config.format_num, Config.format_spell)
        if format_num:
            try:
                text = chinese_to_num(text)
            except Exception as e:
                logger.warning(Notice('diagnostic.text_formatter.itn_conversion_failed_error'), type(e).__name__)
        
        # 3. Adjust language-boundary spacing after ITN establishes numeric boundaries.
        if format_spell:
            text = adjust_space(text)

        return text
