# coding: utf-8
"""
Key mappings.

Convert key names and virtual-key codes, with related constants.
"""

from core.i18n import Notice

from pynput import keyboard
from pynput._util.win32 import KeyTranslator
from . import logger


# Translate virtual-key codes to characters.
_key_translator = KeyTranslator()

# Special virtual-key mappings copied from pynput.
_SPECIAL_KEYS = {
    key.value.vk: key
    for key in keyboard.Key
}

# Numeric keypad mappings (virtual-key code to name).
NUMPAD_KEYS = {
    0x60: 'numpad0',  0x61: 'numpad1',  0x62: 'numpad2',  0x63: 'numpad3',
    0x64: 'numpad4',  0x65: 'numpad5',  0x66: 'numpad6',  0x67: 'numpad7',
    0x68: 'numpad8',  0x69: 'numpad9',
    0x6A: 'numpad_multiply',  # *
    0x6B: 'numpad_add',       # +
    0x6C: 'numpad_separator', # Usually unused.
    0x6D: 'numpad_subtract',  # -
    0x6E: 'numpad_decimal',   # Decimal separator.
    0x6F: 'numpad_divide',    # /
}

# Windows keyboard message constants.
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# Windows mouse message constants.
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

# Key message groups.
KEYBOARD_MESSAGES = (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)
KEY_UP_MESSAGES = (WM_KEYUP, WM_SYSKEYUP)
KEY_DOWN_MESSAGES = (WM_KEYDOWN, WM_SYSKEYDOWN)
MOUSE_MESSAGES = (WM_XBUTTONDOWN, WM_XBUTTONUP)

# Lock keys whose state must be restored after recording.
RESTORABLE_KEYS = {
    'caps_lock',    # Caps Lock.
    'num_lock',     # Num Lock.
    'scroll_lock',  # Scroll Lock.
}


class KeyMapper:
    """Map key names and virtual-key codes."""

    # Cache pynput special-key objects.
    _SPECIAL_KEY_OBJECTS = None

    @classmethod
    def _get_special_key_objects(cls):
        """Initialize and return pynput special keys lazily."""
        if cls._SPECIAL_KEY_OBJECTS is None:
            cls._SPECIAL_KEY_OBJECTS = {
                'caps_lock': keyboard.Key.caps_lock,
                'space': keyboard.Key.space,
                'tab': keyboard.Key.tab,
                'enter': keyboard.Key.enter,
                'esc': keyboard.Key.esc,
                'delete': keyboard.Key.delete,
                'backspace': keyboard.Key.backspace,
                'shift': keyboard.Key.shift,
                'ctrl': keyboard.Key.ctrl,
                'alt': keyboard.Key.alt,
                'cmd': keyboard.Key.cmd,
                'f1': keyboard.Key.f1, 'f2': keyboard.Key.f2, 'f3': keyboard.Key.f3, 'f4': keyboard.Key.f4,
                'f5': keyboard.Key.f5, 'f6': keyboard.Key.f6, 'f7': keyboard.Key.f7, 'f8': keyboard.Key.f8,
                'f9': keyboard.Key.f9, 'f10': keyboard.Key.f10, 'f11': keyboard.Key.f11, 'f12': keyboard.Key.f12,
            }
        return cls._SPECIAL_KEY_OBJECTS

    @staticmethod
    def vk_to_name(vk: int) -> str:
        """
        Convert a virtual-key code to a key name.

        Args:
            vk: Virtual-key code.

        Returns:
            str: Key name in Shortcut.key format.
        """
        # Check pynput's special Key enum first.
        if vk in _SPECIAL_KEYS:
            return _SPECIAL_KEYS[vk].name

        # Check numeric keypad keys.
        if vk in NUMPAD_KEYS:
            return NUMPAD_KEYS[vk]

        # Use pynput KeyTranslator for letters, digits, and symbols.
        try:
            params = _key_translator(vk, is_press=True)
            if 'char' in params and params['char'] is not None:
                return params['char']
        except Exception:
            pass

        # Return vk_ notation for unknown codes.
        return f'vk_{vk}'

    @staticmethod
    def name_to_key(key_name: str):
        """
        Convert a key name to a pynput key object.

        Args:
            key_name: Key name.

        Returns:
            pynput key object, or None.
        """
        # Special keys.
        special_keys = KeyMapper._get_special_key_objects()
        if key_name in special_keys:
            return special_keys[key_name]

        # Single-character keys.
        if len(key_name) == 1:
            return keyboard.KeyCode.from_char(key_name)

        logger.warning(Notice('diagnostic.key_mapper.unknown_key_name', value0=key_name))
        return None
