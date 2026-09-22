# coding: utf-8
"""
Startup tips.

Display startup information through TipsDisplay.
"""

from __future__ import annotations

from core.i18n import Notice, tr

import os

from rich.panel import Panel
from rich.table import Table

from core.client.state import console
from config_client import ClientConfig as Config, __version__
from . import logger



def _format_shortcut_name(key: str) -> str:
    """
    Format a shortcut name for display.

    Args:
        key: Shortcut name, such as 'caps_lock' or 'f12'.

    Returns:
        str: Display name, such as 'CapsLock' or 'F12'.
    """
    # Replace underscores with spaces and apply title case.
    return key.replace('_', ' ').title()


def _get_shortcuts_display() -> str:
    """
    Format all enabled shortcuts for display.

    Returns:
        str: Comma-separated shortcut names.
    """
    enabled_shortcuts = [sc for sc in Config.shortcuts if sc.get('enabled', True)]
    if not enabled_shortcuts:
        return tr('tips.no_shortcuts')

    # Format each shortcut name.
    formatted = [_format_shortcut_name(sc['key']) for sc in enabled_shortcuts]
    return tr('tips.separator').join(formatted)


class TipsDisplay:
    """
    Startup tips display.
    
    Display client startup information.
    """
    
    @staticmethod
    def show_mic_tips() -> None:
        """Display microphone-mode startup tips."""
        shortcuts_display = _get_shortcuts_display()

        details = Table.grid(padding=(0, 2))
        details.add_column(style='ui.label', no_wrap=True)
        details.add_column(style='ui.value')
        details.add_row(tr('tips.shortcuts'), shortcuts_display)
        details.add_row(tr('tips.server'), f'{Config.addr}:{Config.port}')
        details.add_row(tr('tips.directory'), os.getcwd())
        details.add_row(tr('tips.status'), tr('tips.waiting'))
        console.print()
        console.print(Panel(
            details,
            title=tr('tips.mic_title', value0=__version__),
            title_align='left',
            border_style='ui.border',
            padding=(0, 2),
        ))
        console.print(tr('tips.instructions'))

        logger.debug(Notice('diagnostic.tips.microphone_startup_tips_displayed'))
    
    @staticmethod
    def show_file_tips(total: int, formats: str) -> None:
        """Display file-transcription startup tips."""
        details = Table.grid(padding=(0, 2))
        details.add_column(style='ui.label', no_wrap=True)
        details.add_column(style='ui.value')
        details.add_row(tr('tips.server'), f'{Config.addr}:{Config.port}')
        details.add_row(tr('tips.directory'), os.getcwd())
        details.add_row(tr('tips.tasks'), tr('tips.file_count', value0=total, value1=formats))
        console.print()
        console.print(Panel(
            details,
            title=tr('tips.file_title', value0=__version__),
            title_align='left',
            border_style='ui.border',
            padding=(0, 2),
        ))
        
        logger.debug(Notice('diagnostic.tips.file_transcription_startup_tips_displayed'))
