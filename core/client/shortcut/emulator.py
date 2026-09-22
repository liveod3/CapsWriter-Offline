# coding: utf-8
"""
Input emulator.

Simulate keyboard and mouse input asynchronously.
"""

from core.i18n import Notice

from pynput import keyboard, mouse
from . import logger
from core.client.shortcut.key_mapper import KeyMapper



class ShortcutEmulator:
    """
    Shortcut emulator.

    Reuse controller objects to avoid repeated setup.
    """

    def __init__(self):
        """Initialize the emulator."""
        self._keyboard_controller = keyboard.Controller()
        self._mouse_controller = mouse.Controller()
        self._emulating_keys = set()

    def is_emulating(self, key_name: str) -> bool:
        """Return whether the specified key is being simulated."""
        return key_name in self._emulating_keys

    def clear_emulating_flag(self, key_name: str) -> None:
        """Clear the simulation flag."""
        self._emulating_keys.discard(key_name)

    def emulate_key(self, key_name: str) -> None:
        """
        Simulate a keyboard key asynchronously.

        Args:
            key_name: Key name, such as 'caps_lock' or 'f12'.
        """
        self._emulating_keys.add(key_name)

        key_obj = KeyMapper.name_to_key(key_name)
        if key_obj is not None:
            self._keyboard_controller.press(key_obj)
            self._keyboard_controller.release(key_obj)
            logger.debug(Notice('diagnostic.emulator.key_replay_succeeded', value0=key_name))
        else:
            logger.warning(Notice('diagnostic.emulator.unknown_key_replay_skipped', value0=key_name))

    def emulate_mouse_click(self, button_name: str) -> None:
        """
        Simulate a mouse button asynchronously.

        Args:
            button_name: Mouse button name ('x1' or 'x2').
        """
        self._emulating_keys.add(button_name)

        # Map names to pynput mouse buttons.
        button_map = {
            'x1': mouse.Button.x1,
            'x2': mouse.Button.x2
        }

        if button_name in button_map:
            button = button_map[button_name]
            self._mouse_controller.press(button)
            self._mouse_controller.release(button)
            logger.debug(Notice('diagnostic.emulator.mouse_button_replay_succeeded', value0=button_name))
        else:
            logger.warning(Notice('diagnostic.emulator.unknown_mouse_button_replay_skipped', value0=button_name))
