"""
GPU boost management.

Lock and reset GPU memory clocks to reduce startup latency.
"""

from core.i18n import Notice

import subprocess
import time
import ctypes
from config_server import ServerConfig as Config
from . import logger


class GpuBoostManager:
    """
    GPU boost manager.

    Check administrator rights, execute boost/reset commands, and handle idle expiry.
    """

    def __init__(self, state):
        self.state = state

    # Public methods.

    def handle_command(self, task):
        """Process a GPU boost command task."""
        if task.command != 'gpu_boost':
            return
        if not self._check_admin():
            logger.warning(Notice('diagnostic.gpu_boost.administrator_privileges_required_to_enable_gpu_boost'))
            return
        if self.state.gpu_boosted:
            self.state.gpu_last_active = 0
            return

        logger.info(Notice('diagnostic.gpu_boost.gpu_boost_command', value0=Config.gpu_boost_cmd))
        subprocess.run(Config.gpu_boost_cmd, shell=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.state.gpu_boosted = True
        self.state.gpu_last_active = 0  # Zero means boosted but not yet used by an audio task.

    def check_idle(self):
        """Reset GPU boost after the idle timeout."""
        if not Config.gpu_boost_enabled or not self.state.gpu_boosted:
            return
        # Keep a fresh boost while gpu_last_active is zero and no audio task has used it.
        if self.state.gpu_last_active <= 0:
            return

        idle_time = time.time() - self.state.gpu_last_active
        if idle_time <= Config.gpu_unboost_timeout:
            return

        if not self._check_admin():
            logger.warning(Notice('diagnostic.gpu_boost.administrator_privileges_required_to_disable_gpu_boost'))
            return

        logger.info(Notice('diagnostic.gpu_boost.gpu_idle_for_s_disabling_boost', value0=idle_time, value1=Config.gpu_unboost_cmd))
        subprocess.run(Config.gpu_unboost_cmd, shell=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.state.gpu_boosted = False
        self.state.gpu_last_active = 0.0

    # Internal methods.

    @staticmethod
    def _check_admin() -> bool:
        """Return whether the process has administrator rights."""
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
