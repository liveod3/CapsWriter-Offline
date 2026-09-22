# coding: utf-8
"""
Global hotkey manager.

Use pynput GlobalHotKeys in place of the keyboard library.

Example:
    from core.client.global_hotkey import GlobalHotkeyManager

    manager = GlobalHotkeyManager()
    manager.register('<esc>', lambda: print('ESC pressed'))
    manager.start()
"""
from __future__ import annotations

from core.i18n import Notice

import threading
from typing import Callable, Dict, Optional

from pynput import keyboard

from . import logger



class GlobalHotkeyManager:
    """
    Global hotkey manager.

    Support dynamic shortcut registration through pynput GlobalHotKeys.
    
    Integration properties:
    - Uses the same library as other input handling.
    - Requires no additional dependency.
    - Uses pynput's platform backends.
    """

    # Singleton instance.
    _instance: Optional[GlobalHotkeyManager] = None
    _lock = threading.Lock()

    def __new__(cls) -> GlobalHotkeyManager:
        """Create the singleton instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self._hotkeys: Dict[str, Callable] = {}
        self._listener: Optional[keyboard.GlobalHotKeys] = None
        self._running = False
        self._initialized = True
        logger.debug(Notice('diagnostic.global_hotkey.globalhotkeymanager_initialized'))

    def register(self, key_str: str, callback: Callable) -> None:
        """
        Register a global shortcut.

        Args:
            key_str: pynput shortcut syntax, such as '<esc>' or '<ctrl>+<alt>+h'.
            callback: Function called when the shortcut is pressed.
        """
        self._hotkeys[key_str] = callback
        logger.debug(Notice('diagnostic.global_hotkey.global_hotkey_registered', value0=key_str))
        
        # Restart an active listener to apply the updated shortcut set.
        if self._running:
            self._restart_listener()

    def unregister(self, key_str: str) -> bool:
        """
        Unregister a global shortcut.

        Args:
            key_str: Shortcut string.

        Returns:
            Whether the shortcut was removed.
        """
        if key_str in self._hotkeys:
            del self._hotkeys[key_str]
            logger.debug(Notice('diagnostic.global_hotkey.global_hotkey_unregistered', value0=key_str))
            
            if self._running:
                self._restart_listener()
            return True
        return False

    def start(self) -> None:
        """Start listening for global shortcuts."""
        if self._running:
            logger.debug(Notice('diagnostic.global_hotkey.globalhotkeymanager_already_running'))
            return
        
        if not self._hotkeys:
            logger.warning(Notice('diagnostic.global_hotkey.no_hotkeys_registered_startup_skipped'))
            return
        
        self._running = True
        self._start_listener()
        logger.info(Notice('diagnostic.global_hotkey.globalhotkeymanager_started_with_hotkeys', value0=len(self._hotkeys)))

    def stop(self) -> None:
        """Stop listening for global shortcuts."""
        self._running = False
        if self._listener:
            try:
                self._listener.stop()
            except Exception as e:
                logger.warning(Notice('diagnostic.global_hotkey.failed_to_stop_globalhotkeys_listener', value0=e))
            self._listener = None
        logger.info(Notice('diagnostic.global_hotkey.globalhotkeymanager_stopped'))

    def _start_listener(self) -> None:
        """Start the listener."""
        if not self._hotkeys:
            return
        
        try:
            self._listener = keyboard.GlobalHotKeys(self._hotkeys)
            self._listener.start()
            logger.debug(Notice('diagnostic.global_hotkey.globalhotkeys_listener_started', value0=list(self._hotkeys.keys())))
        except Exception as e:
            logger.error(Notice('diagnostic.global_hotkey.failed_to_start_globalhotkeys_listener', value0=e))
            self._listener = None

    def _restart_listener(self) -> None:
        """Restart the listener after shortcut changes."""
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        
        if self._running and self._hotkeys:
            self._start_listener()


# Global singleton instance.
_global_hotkey_manager: Optional[GlobalHotkeyManager] = None


def get_global_hotkey_manager() -> GlobalHotkeyManager:
    """Return the global hotkey manager singleton."""
    global _global_hotkey_manager
    if _global_hotkey_manager is None:
        _global_hotkey_manager = GlobalHotkeyManager()
    return _global_hotkey_manager
