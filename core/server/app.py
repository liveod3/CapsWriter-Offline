# coding: utf-8
"""
CapsWriter server facade.

Coordinate ProcessManager and SocketManager.
Initialize application lifecycle and tray integration,
and coordinate subprocess and WebSocket startup and shutdown.
"""

from core.i18n import Notice, tr

import os
import asyncio
import queue
import threading
from pathlib import Path
from config_server import ServerConfig as Config, __version__
from .state import ServerState, console
from core.tools.signal_handler import register_signal
from .worker.process_manager import ProcessManager
from .connection.server_manager import SocketManager
from .ui.tray_manager import TrayManager
from . import logger

class CapsWriterServer:
    """
    CapsWriter server facade.
    
    Expose start() as the main entry point.
    """
    def __init__(self):
        from core.i18n import set_language
        from core.i18n.preference import client_language_reloader
        import config_client
        # Set the working directory.
        self.base_dir = Path(__file__).parents[2]
        os.chdir(self.base_dir)
        self.client_language_reload = client_language_reloader(
            self.base_dir / 'config_client.py', config_client, self._report_config,
        )
        set_language(getattr(self.client_language_reload.target, 'ui_language',
                             getattr(Config, 'ui_language', 'auto')))

        # Initialize the event loop.
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Initialize shared state.
        self.state = ServerState(app=self)

        # Initialize settings and components.
        self.process_manager = ProcessManager(self)
        self.socket_manager = SocketManager(self)
        self.tray_manager = TrayManager(self)

        self.version = __version__
        self.is_alive = False
        self._owner_thread = threading.get_ident()
        self._stop_requested = threading.Event()
        self._cleaned_up = False
        import config_server
        from config_templates import config_server_template
        from core.config_reload import ConfigReloader, SERVER_LIVE
        self.config_reload = ConfigReloader(
            self.base_dir / 'config_server.py', config_server, config_server_template,
            'ServerConfig', SERVER_LIVE, self._report_config,
        )

    def _report_config(self, message):
        from core.i18n import localize_notice
        logger.info(message, extra={'console_handled': True})
        console.print(localize_notice(message), markup=False)

    def apply_config_reload(self):
        # AudioCache snapshots these settings on the same event loop at admission.
        changed = self.config_reload.apply()
        follower = getattr(self, 'client_language_reload', None)
        client_changed = follower.apply() if follower is not None else ()
        from core.i18n import get_language, set_language
        previous_language = get_language()
        fallback = getattr(Config, 'ui_language', 'auto')
        preference = getattr(follower.target, 'ui_language', fallback) if follower else fallback
        language = set_language(preference)
        if language != previous_language:
            from core.ui.tray import refresh_language
            self.process_manager.publish_ui_language(language)
            refresh_language()
        if client_changed:
            self._report_config(Notice('language.server_following'))
        if changed:
            self._report_config(Notice('config.server_applied', fields=', '.join(changed)))


    def _print_banner(self):
        """Display startup information."""
        console.line(2)
        console.rule(tr('server.banner')); console.line()
        console.print(tr('server.version', value0=self.version), end='\n\n')
        console.print(tr('server.project'), end='\n\n')
        console.print(tr('server.directory', value0=self.base_dir), end='\n\n')
        console.print(tr('server.address', value0=Config.addr, value1=Config.port), end='\n\n')

    def stop(self):
        """Request shutdown on the loop owner; reap processes after the loop drains."""
        self._stop_requested.set()
        if threading.get_ident() != self._owner_thread:
            if self.loop.is_running():
                try:
                    self.loop.call_soon_threadsafe(self.stop)
                except RuntimeError:
                    pass  # The owner is already closing the loop.
            return
        self.is_alive = False
        if self.loop.is_running():
            self.socket_manager.stop()

    def _cleanup(self):
        """Called by start's finally, never reentrantly from a signal/tray callback."""
        self.is_alive = False
        if self._cleaned_up:
            return
        self._cleaned_up = True

        logger.info("=" * 50)
        logger.info(Notice('diagnostic.app.cleaning_up_server_resources'))

        try:
            self.state.queue_out.put_nowait(None)
        except (queue.Full, OSError, EOFError, ValueError) as exc:
            logger.debug(Notice('diagnostic.app.result_shutdown_signal_unavailable'), type(exc).__name__)

        # A broken queue/component must not skip the remaining cleanup owners.
        for name, component in (('network', self.socket_manager),
                                ('worker', self.process_manager),
                                ('tray', self.tray_manager)):
            try:
                component.stop()
            except Exception as exc:
                logger.error(Notice('diagnostic.app.server_cleanup_failed_component_error'), name, type(exc).__name__)

        logger.info(Notice('diagnostic.app.server_resource_cleanup_complete'))
        console.print(tr('server.goodbye'))


    def start(self):
        """
        Start the server synchronously.
        
        Register signal handlers, start subprocesses, and enter the network loop.
        """
        # Ignore repeated activation.
        if self.is_alive: return
        self._owner_thread = threading.get_ident()
        self._stop_requested = threading.Event()
        self._cleaned_up = False

        # Validate security settings before starting tray or model subprocesses.
        try:
            self.socket_manager.prepare()
        except ValueError as exc:
            logger.critical(Notice('diagnostic.app.invalid_server_network_configuration', value0=exc))
            console.print(tr('server.network_error', value0=exc))
            return

        self.is_alive = True

        # Register shutdown signal handlers.
        register_signal(self.stop)

        try:
            self.tray_manager.start()
            self._print_banner()
            self.process_manager.start()
            if self.is_alive and not self._stop_requested.is_set():
                for name in ('config_reload', 'client_language_reload'):
                    reloader = getattr(self, name, None)
                    if reloader is not None:
                        reloader.task = self.loop.create_task(
                            reloader.watch(self.apply_config_reload))
                self.loop.run_until_complete(self.socket_manager.start())
        finally:
            for name in ('config_reload', 'client_language_reload'):
                reloader = getattr(self, name, None)
                if reloader is not None:
                    self.loop.run_until_complete(reloader.close())
            # Sender failure ends the listener; release model processes and tray too.
            self._cleanup()
            self.loop.close()
