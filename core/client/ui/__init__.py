# coding: utf-8
"""
Client UI facade.

Provide one entry point for client UI components.
Inject the client logger into shared UI components on import
and re-export commonly used components.
"""

from .. import logger
import core.ui

# 1. Inject the client logger into shared UI components.
core.ui.set_ui_logger(logger)

# 2. Export client-specific UI components.
from core.client.ui.tips import TipsDisplay

# 3. Re-export shared UI components.
# Client modules can import these from core.client.ui.
from core.ui import (
    enable_min_to_tray,
    stop_tray,
)

# 4. Export the menu handler for startup.

__all__ = [
    'logger',
    'TipsDisplay',
    'enable_min_to_tray',
    'stop_tray',
]
