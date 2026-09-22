# coding: utf-8

from core.i18n import Notice
import asyncio
from . import logger
from ..ui import TipsDisplay
from config_client import ClientConfig as Config, __version__


class MicRunner:
    """
    Initialize microphone resources, consume recognition results, and monitor lifecycle.
    """
    def __init__(self, app):
        self.app = app
        self.processor = None

    @property
    def state(self):
        return self.app.state

    @property
    def ws_manager(self):
        return self.app.ws

    @property
    def tray_manager(self):
        return self.app.tray

    async def start_resources(self):
        """Initialize microphone hardware, shortcuts, and the tray UI."""
        if self.app._stopping:
            return
        # 1. Tray.
        self.tray_manager.start()

        # 2. UI feedback.
        TipsDisplay.show_mic_tips()

        # 3. Audio stream and shortcut listeners.
        await asyncio.to_thread(self.app.stream.start)
        if self.app._stopping:
            return
        self.app.shortcut.start()
        
        # 4. Optional UDP control.
        if Config.udp_control:
            self.app.udp.start()

        # 5. Text-action cancellation shortcut.
        self.app.llm.start()

        # 6. Idle suspension monitor.
        self.app.start_idle_suspend_monitor()

    async def run(self):
        """Run microphone mode."""
        
        logger.info("=" * 50)
        logger.info(Notice('diagnostic.mic_runner.capswriter_offline_client_microphone_mode', value0=__version__))
        logger.info(Notice('diagnostic.mic_runner.log_level', value0=Config.log_level))
        
        # 1. Start resources.
        await self.start_resources()
        if self.app._stopping:
            return
        
        # 2. Start the processor, which owns the connection and processing loop.
        
        from ..output import ResultProcessor
        self.processor = ResultProcessor(self.app)
        await self.processor.start()
            
