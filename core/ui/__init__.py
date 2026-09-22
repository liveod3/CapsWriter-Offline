"""Shared UI utilities.

Provide floating Toast notifications and system tray support.
Share components between client and server with an injected logger.
"""
import logging
from typing import Any

# ============================================================
# Logger proxy.
# ============================================================

class _LoggerProxy:
    """
    Forward logger attributes dynamically through __getattr__.
    Allow imports before the actual logger is injected.
    """
    def __init__(self):
        self._target = logging.getLogger('core.ui')  # Default logger.

    def set_target(self, logger):
        """Inject the actual logger."""
        self._target = logger

    def __getattr__(self, name):
        """Forward attribute access to the actual logger."""
        return getattr(self._target, name)

# Create the proxy.
logger = _LoggerProxy()

def set_ui_logger(real_logger):
    """Set the logger used by shared UI components."""
    logger.set_target(real_logger)

# ============================================================
# Export components.
# ============================================================

from .toast import toast, toast_stream, ToastMessage, ToastMessageManager
from .tray import enable_min_to_tray, stop_tray, set_recording_state, set_dictation_paused
from .recording_indicator import show_recording_indicator, hide_recording_indicator, show_status_hint

__all__ = [
    'logger',
    'set_ui_logger',
    'toast',
    'toast_stream',
    'ToastMessage',
    'ToastMessageManager',
    'enable_min_to_tray',
    'stop_tray',
    'set_recording_state',
    'set_dictation_paused',
    'show_recording_indicator',
    'hide_recording_indicator',
    'show_status_hint',
]
