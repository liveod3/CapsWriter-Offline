# coding: utf-8
"""
Shortcut configuration dataclass.

Define Shortcut settings and behavior.
Avoid circular imports by keeping configuration imports out of this module.
"""

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class Shortcut:
    """
    Shortcut configuration.

    Attributes:
        key: pynput key name, such as 'caps_lock', 'a', 'f1', or 'ctrl+shift+a'.
        type: Input type, 'keyboard' or 'mouse'.
        suppress: Prevent other applications from receiving the original key event.
        hold_mode: True records while held; False toggles recording on each press.
        threshold: Activation delay in seconds to avoid accidental input; None uses Config.threshold.
        enabled: Enable this shortcut.

    Notes:
        - Unsuppressed lock keys (CapsLock/NumLock/ScrollLock) are replayed to restore their state.
        - Suppressed short presses are replayed on release to preserve normal key behavior.
    """
    key: str
    type: Literal['keyboard', 'mouse'] = 'keyboard'
    suppress: bool = False
    hold_mode: bool = True
    threshold: Optional[float] = None  # None uses Config.threshold.
    enabled: bool = True

    def __post_init__(self):
        """Normalize configuration after initialization."""
        # Normalize the key name.
        self.key = self._normalize_key(self.key)

    def get_threshold(self, default_threshold: float = 0.3) -> float:
        """
        Return the shortcut activation threshold.

        Args:
            default_threshold: Fallback threshold.

        Returns:
            float: Threshold in seconds.
        """
        return self.threshold if self.threshold is not None else default_threshold

    @staticmethod
    def _normalize_key(key: str) -> str:
        """
        Normalize a key name.

        Args:
            key: Original key name.

        Returns:
            str: Normalized name in pynput format.
        """
        # Convert to lowercase.
        key = key.lower().strip()

        # Replace common aliases.
        aliases = {
            'capslock': 'caps_lock',
            'caps lock': 'caps_lock',
            ' ': 'space',
            'control': 'ctrl',
        }

        for old, new in aliases.items():
            key = key.replace(old, new)

        # Normalize modifier aliases for pynput.
        # Retain forms such as 'left ctrl'.

        return key

    def is_toggle_key(self) -> bool:
        """
        Return whether this is a lock key requiring state restoration.

        Returns:
            bool: Whether the key has a restorable toggle state.

        RESTORABLE_KEYS defines the supported lock keys.
        """
        from core.client.shortcut.key_mapper import RESTORABLE_KEYS

        # Check for a restorable lock key in the name.
        return any(toggle_key in self.key for toggle_key in RESTORABLE_KEYS)


# Common shortcut presets.
@dataclass
class CommonShortcuts:
    """Common shortcut presets."""

    @staticmethod
    def caps_lock() -> Shortcut:
        """Caps Lock preset; not the application default."""
        return Shortcut(
            key='caps_lock',
            type='keyboard',
            suppress=False,
            hold_mode=True,
            threshold=0.3
        )

    @staticmethod
    def mouse_x2() -> Shortcut:
        """Mouse X2 (Forward) preset."""
        return Shortcut(
            key='x2',
            type='mouse',
            suppress=True,
            hold_mode=True,
            threshold=0.3,
        )

    @staticmethod
    def f12() -> Shortcut:
        """F12 preset."""
        return Shortcut(
            key='f12',
            type='keyboard',
            suppress=False,
            hold_mode=True,
            threshold=0.3
        )

    @staticmethod
    def space() -> Shortcut:
        """Space preset."""
        return Shortcut(
            key='space',
            type='keyboard',
            suppress=False,
            hold_mode=True,
            threshold=0.3
        )
