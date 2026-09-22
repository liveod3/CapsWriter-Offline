# coding: utf-8
"""
UDP control module.

Let external applications start and stop recording through UDP commands.

Command protocol:
- START: Start recording.
- STOP: Stop recording.
"""

from __future__ import annotations

from core.i18n import Notice

import socket
import threading
from typing import TYPE_CHECKING

from config_client import ClientConfig as Config
from . import logger

if TYPE_CHECKING:
    from core.client.shortcut.shortcut_manager import ShortcutManager



class UDPController:
    """
    UDP controller.
    
    Listen for recording commands on a background UDP thread.
    """
    
    def __init__(self, shortcut_manager: ShortcutManager):
        """
        Initialize the UDP controller.

        Args:
            shortcut_manager: Shortcut manager used to control recording.
        """
        self.manager = shortcut_manager
        self.running = False
        self._thread = None
        self._sock = None
    
    def start(self) -> None:
        """Start the UDP listener."""
        if self.running and self._thread and self._thread.is_alive():
            logger.debug(Notice('diagnostic.udp_control.udp_controller_already_running_startup_skipped'))
            return
        
        self.running = True
        self._thread = threading.Thread(target=self._listen, daemon=True, name="UDPController")
        self._thread.start()
        logger.info(Notice('diagnostic.udp_control.udp_controller_started_on_port', value0=Config.udp_control_port))
    
    def stop(self) -> None:
        """Stop the UDP listener."""
        if not self.running:
            return
            
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            finally:
                self._sock = None
        logger.info(Notice('diagnostic.udp_control.udp_controller_stopped'))
    
    def _listen(self) -> None:
        """Listen for incoming commands."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((Config.udp_control_addr, Config.udp_control_port))
            self._sock.settimeout(0.5)
            
            logger.debug(Notice('diagnostic.udp_control.udp_controller_bound_to', value0=Config.udp_control_addr, value1=Config.udp_control_port))
            
            while self.running:
                try:
                    data, addr = self._sock.recvfrom(1024)
                    command = data.decode('utf-8').strip().upper()
                    self._handle_command(command, addr)
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        logger.error(Notice('diagnostic.udp_control.udp_controller_receive_failed', value0=e))
        
        except Exception as e:
            logger.error(Notice('diagnostic.udp_control.udp_controller_startup_failed', value0=e))
        finally:
            if self._sock:
                self._sock.close()
    
    def _handle_command(self, command: str, addr: tuple) -> None:
        """
        Handle an incoming command.

        Args:
            command: Command string (START/STOP).
            addr: Sender address.
        """
        state = self.manager.state

        if command == 'START':
            if state.dictation_paused:
                logger.debug(Notice('diagnostic.udp_control.udp_control_start_ignored_dictation_paused'))
                return

            if not state.recording:
                logger.info(Notice('diagnostic.udp_control.udp_control_start_recording_from', value0=addr[0], value1=addr[1]))
                # Start recording through the first available shortcut task.
                if self.manager.tasks:
                    first_task = next(iter(self.manager.tasks.values()))
                    first_task.launch()
            else:
                logger.debug(Notice('diagnostic.udp_control.udp_control_start_ignored_already_recording'))

        elif command == 'STOP':
            if state.recording:
                logger.info(Notice('diagnostic.udp_control.udp_control_stop_recording_from', value0=addr[0], value1=addr[1]))
                # Stop all recording tasks.
                for task in self.manager.tasks.values():
                    if task.is_recording:
                        task.finish()
            else:
                logger.debug(Notice('diagnostic.udp_control.udp_control_stop_ignored_not_recording'))

        else:
            logger.warning(Notice('diagnostic.udp_control.udp_control_unknown_command_from', value0=command, value1=addr[0], value2=addr[1]))
