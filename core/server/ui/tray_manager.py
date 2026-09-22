# coding: utf-8
from __future__ import annotations

from core.i18n import Notice
import os
from typing import TYPE_CHECKING
from config_server import ServerConfig as Config
from ..state import console
from .. import logger # Server module logger
if TYPE_CHECKING:
    from ..app import CapsWriterServer


class TrayManager:
    """
    Initialize the system tray, construct menus, and handle callbacks.
    """
    def __init__(self, app: CapsWriterServer):
        self.app = app

    def start(self):
        """Initialize the system tray icon."""
        if not Config.enable_tray:
            return

        try:
            from . import enable_min_to_tray
        except ImportError as e:
            logger.warning(Notice('diagnostic.tray_manager.tray_module_import_failed_tray_disabled', value0=e))
            return

        # Resolve the icon path.
        icon_path = os.path.join(self.app.base_dir, 'assets', 'server-icon.ico')
        
        # Enable the tray.
        enable_min_to_tray(
            'CapsWriter Server',
            icon_path,
            exit_callback=self._request_exit
        )
        logger.info(Notice('diagnostic.tray_manager.tray_icon_enabled'))

    def _request_exit(self, icon=None, item=None):
        """Handle the tray exit action."""
        logger.info(Notice('diagnostic.tray_manager.tray_exit_requested_cleaning_up_resources'))
        self.app.stop()

    def stop(self):
        """Stop the tray icon."""
        if not Config.enable_tray:
            return
            
        try:
            from core.ui.tray import stop_tray
            stop_tray()
            logger.info(Notice('diagnostic.tray_manager.traymanager_tray_icon_removed'))
        except Exception as e:
            logger.debug(Notice('diagnostic.tray_manager.traymanager_failed_to_remove_tray_icon', value0=e))
