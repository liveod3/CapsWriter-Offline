# coding: utf-8
"""
System tray integration.

Hide the console in the system tray.
Supported only on Windows.

Features:
- Disable the console close button to prevent accidental closure.
- Hide the console when minimized.
- Toggle console visibility from the tray icon.
- Exit through the tray menu.

Import pystray lazily because headless Linux cannot initialize its GUI backend.
"""

from core.i18n import Notice, lazy, tr

import os
import sys
import time
import threading
import platform
import subprocess
from typing import Optional
from . import logger, set_ui_logger

# Application-provided exit callback.
_exit_callback = None

# Availability detected when enabling tray support.
_tray_available = None

def _set_exit_callback(callback):
    """Set the exit callback."""
    global _exit_callback
    _exit_callback = callback

def _get_exit_callback():
    return _exit_callback


def _check_tray_available() -> bool:
    """
    Check whether tray integration is available.
    
    Returns:
        bool: Whether the feature is available.
    """
    global _tray_available
    
    if _tray_available is not None:
        return _tray_available
    
    # This integration requires Windows.
    if platform.system() != 'Windows':
        _tray_available = False
        return False
    
    # Try importing pystray.
    try:
        import pystray
        from PIL import Image
        _tray_available = True
    except ImportError as e:
        logger.warning(Notice('diagnostic.tray.tray_unavailable', value0=e))
        _tray_available = False
    except Exception as e:
        logger.warning(Notice('diagnostic.tray.tray_detection_failed', value0=e))
        _tray_available = False
    
    return _tray_available


# Lazily initialized Windows API bindings.
_win_api_initialized = False
user32 = None
kernel32 = None

SW_HIDE = 0
SW_RESTORE = 9
SW_SHOW = 5
SC_CLOSE = 0xF060
MF_BYCOMMAND = 0x00000000
GA_ROOT = 3


def _init_win_api():
    """Initialize Windows API bindings."""
    global _win_api_initialized, user32, kernel32
    
    if _win_api_initialized:
        return
    
    if platform.system() != 'Windows':
        return
    
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        from ctypes import wintypes as W
        kernel32.GetConsoleWindow.restype = W.HWND
        user32.GetAncestor.argtypes = [W.HWND, W.UINT]
        user32.GetAncestor.restype = W.HWND
        user32.GetSystemMenu.argtypes = [W.HWND, W.BOOL]
        user32.GetSystemMenu.restype = W.HMENU
        user32.DeleteMenu.argtypes = [W.HMENU, W.UINT, W.UINT]
        for name in ("IsIconic", "IsWindowVisible", "SetForegroundWindow"):
            getattr(user32, name).argtypes = [W.HWND]
        user32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
        _win_api_initialized = True
    except Exception as e:
        logger.warning(Notice('diagnostic.tray.windows_api_initialization_failed', value0=e))


# Module state.
_tray_instance: Optional['_TraySystem'] = None
_lock = threading.Lock()
_tray_recording = False
_tray_paused = False


def _get_console_hwnd():
    _init_win_api()
    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        # Windows Terminal's GetConsoleWindow can return an internal window.
        # Use the top-level ancestor for close-button and taskbar visibility operations.
        # For classic consoles, GetAncestor(hwnd, GA_ROOT) returns the same handle.
        root_hwnd = user32.GetAncestor(hwnd, GA_ROOT)
        if root_hwnd:
            return root_hwnd
    return hwnd


def _disable_close_button(hwnd: int) -> None:
    """Disable the window close button."""
    if user32 is None:
        return
    h_menu = user32.GetSystemMenu(hwnd, False)
    if h_menu:
        user32.DeleteMenu(h_menu, SC_CLOSE, MF_BYCOMMAND)


def _enable_close_button(hwnd: int) -> None:
    """Restore the window close button."""
    if user32 is None:
        return
    user32.GetSystemMenu(hwnd, True)


def _is_window_minimized(hwnd: int) -> bool:
    """Return whether the window is minimized."""
    if user32 is None:
        return False
    return user32.IsIconic(hwnd) != 0


def _is_window_visible(hwnd: int) -> bool:
    """Return whether the window is visible."""
    if user32 is None:
        return False
    return user32.IsWindowVisible(hwnd) != 0


def _add_recording_badge(image):
    """Overlay a red recording badge at the icon's bottom right."""
    from PIL import ImageDraw

    image = image.convert('RGBA')
    draw = ImageDraw.Draw(image)
    center_x = image.width - 12
    center_y = image.height - 12
    outer_radius = 10
    inner_radius = 8

    # A light outline keeps the badge visible across icon and taskbar themes.
    draw.ellipse(
        (
            center_x - outer_radius,
            center_y - outer_radius,
            center_x + outer_radius,
            center_y + outer_radius,
        ),
        fill=(255, 248, 240, 255),
    )
    draw.ellipse(
        (
            center_x - inner_radius,
            center_y - inner_radius,
            center_x + inner_radius,
            center_y + inner_radius,
        ),
        fill=(255, 59, 48, 255),
    )
    return image


def _create_icon(icon_path: Optional[str] = None, recording: bool = False):
    """
    Create the tray image.
    
    Load the application icon and overlay the recording badge when active.
    
    Args:
        icon_path: Required application icon path.
        recording: Show the recording badge.
        
    Returns:
        PIL Image instance.
    """
    from PIL import Image

    if not icon_path:
        raise ValueError(Notice('validation.tray.tray_icon_path_is_missing'))
    if not os.path.exists(icon_path):
        raise FileNotFoundError(Notice('validation.tray.tray_icon_does_not_exist', value0=icon_path))

    try:
        with Image.open(icon_path) as source:
            image = source.convert('RGBA')
        image = image.resize((64, 64), Image.Resampling.LANCZOS)
    except Exception as error:
        raise RuntimeError(Notice('validation.tray.failed_to_load_tray_icon', value0=icon_path)) from error

    if recording:
        return _add_recording_badge(image)
    return image


class _TraySystem:
    """Internal tray implementation."""
    
    def __init__(self, name: Optional[str] = None, icon_path: Optional[str] = None, more_options: list = None):
        # Import pystray lazily.
        import pystray
        from pystray import MenuItem as item
        from .menu_model import MenuAction
        from .tray_native import NativeMenuIcon
        
        self.hwnd = _get_console_hwnd()
        self.should_exit = False
        self.title = name if name else (os.path.basename(sys.argv[0]) or "Console App")
        self._title_id = {'CapsWriter Client': 'app.client', 'CapsWriter Server': 'app.server'}.get(self.title)
        self._icon_path = icon_path

        # Disable the close button.
        if self.hwnd:
            _disable_close_button(self.hwnd)

        # Define the menu.
        menu_items = [
            item(lambda _item: self.display_title, lambda: None, enabled=False),
            MenuAction(lambda _item: tr('tray.console.hide') if _is_window_visible(self.hwnd) else tr('tray.console.show'),
                       self.toggle_window, lazy('tray.console.tip'),
                       'console', default=True).to_item(),
        ]

        # Append additional actions.
        if more_options:
            for option in more_options:
                if isinstance(option, MenuAction):
                    menu_items.append(option.to_item())
                else:
                    opt_name, opt_func = option
                    menu_items.append(item(opt_name, opt_func))

        menu_items.append(pystray.Menu.SEPARATOR)
        menu_items.append(MenuAction(lazy('tray.restart.client' if self._title_id == 'app.client' else 'tray.restart.server'),
                                    self.on_restart, lazy('tray.restart.tip'),
                                    'restart').to_item())
        menu_items.append(MenuAction(lazy('tray.quit'), self.on_exit, lazy('tray.quit.tip'),
                                    'quit').to_item())

        self.icon = NativeMenuIcon(
            "console_tray",
            _create_icon(icon_path),
            title=self.display_title,
            menu=pystray.Menu(*menu_items)
        )

    @property
    def display_title(self):
        return tr(self._title_id) if self._title_id else self.title

    def toggle_window(self) -> None:
        """Toggle console visibility."""
        if not self.hwnd or user32 is None:
            return

        if _is_window_visible(self.hwnd):
            user32.ShowWindow(self.hwnd, SW_HIDE)
        else:
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            user32.SetForegroundWindow(self.hwnd)

    def monitor_loop(self) -> None:
        """Monitor console minimization."""
        while not self.should_exit:
            if self.hwnd and user32:
                # Hide a visible minimized console in the tray.
                if _is_window_visible(self.hwnd) and _is_window_minimized(self.hwnd):
                    user32.ShowWindow(self.hwnd, SW_HIDE)
            time.sleep(0.2)

    def on_restart(self, icon, item) -> None:
        """Start a replacement process, then exit this process."""
        logger.info(Notice('diagnostic.tray.tray_restart_requested_preparing_to_restart_application'))
        try:
            if getattr(sys, 'frozen', False):
                cmd = sys.argv
            else:
                cmd = [sys.executable] + sys.argv
            subprocess.Popen(cmd)
        except Exception as e:
            logger.error(Notice('diagnostic.tray.restart_failed', value0=e))
            return

        # Exit through the application callback only after replacement startup succeeds.
        exit_callback = _get_exit_callback()
        if exit_callback:
            try:
                exit_callback()
            except Exception as e:
                logger.error(Notice('diagnostic.tray.exit_callback_failed_during_restart', value0=e))

    def on_exit(self, icon, item) -> None:
        """Handle tray exit."""
        exit_callback = _get_exit_callback()

        logger.info(Notice('diagnostic.tray_manager.tray_exit_requested_cleaning_up_resources'))

        # 1. Stop the monitor loop.
        self.should_exit = True
        logger.debug(Notice('diagnostic.tray.tray_exit_flag_set'))

        # 2. Restore the close button and show the console.
        if self.hwnd and user32:
            _enable_close_button(self.hwnd)
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            logger.debug(Notice('diagnostic.tray.window_visibility_restored'))

        # 3. Request application shutdown through the callback.
        if exit_callback:
            try:
                logger.debug(Notice('diagnostic.tray.calling_exit_callback'))
                exit_callback()
                logger.info(Notice('diagnostic.tray.exit_callback_completed'))
            except Exception as e:
                logger.error(Notice('diagnostic.tray.exit_callback_failed', value0=e))



        # 5. Stop the tray icon.
        try:
            logger.debug(Notice('diagnostic.tray.stopping_tray_icon_thread'))
            self.icon.stop()
            logger.debug(Notice('diagnostic.tray.tray_icon_thread_stopped'))
        except Exception as e:
            logger.warning(Notice('diagnostic.tray.failed_to_stop_tray_icon', value0=e))

    def start(self) -> None:
        """Start tray integration."""
        # Tray icon thread.
        t_tray = threading.Thread(target=self.icon.run, daemon=False)
        t_tray.start()

        # Status monitor thread.
        t_monitor = threading.Thread(target=self.monitor_loop, daemon=True)
        t_monitor.start()

        # Hide the console at startup.
        self.toggle_window()


def enable_min_to_tray(name: Optional[str] = None, icon_path: Optional[str] = None, exit_callback=None, more_options: list = None) -> None:
    """
    Enable minimize-to-tray behavior.

    Do nothing when no console window exists, such as under .pyw.
    Skip unsupported platforms and headless environments.

    Args:
        name: Tray display name; defaults to the application name.
        icon_path: Required application icon path.
        exit_callback: Callback for the tray exit action.
        more_options: Additional (label, callback) menu entries.
    """
    global _tray_instance

    global _tray_instance

    # Set the exit callback.
    if exit_callback is not None:
        _set_exit_callback(exit_callback)

    # Check tray availability.
    if not _check_tray_available():
        logger.info(Notice('diagnostic.tray.tray_unavailable_enable_skipped'))
        return

    # Configure DPI awareness.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    with _lock:
        if _tray_instance is not None:
            return  # Already started.

        if not _get_console_hwnd():
            return  # No console window.

        _tray_instance = _TraySystem(name, icon_path, more_options)
        _tray_instance.start()



def stop_tray() -> None:
    """Stop the tray icon."""
    global _tray_instance, _tray_recording, _tray_paused
    if _tray_instance and _tray_instance.icon:
        _tray_instance.should_exit = True
        try:
            _tray_instance.icon.stop()
        except Exception:
            pass
    _tray_instance = None
    _tray_recording = False
    _tray_paused = False


def set_recording_state(recording: bool) -> None:
    """Update tray recording state under the shared lock."""
    global _tray_recording
    _tray_recording = recording
    _refresh_tray_status()


def set_dictation_paused(paused: bool) -> None:
    """Update tray pause state under the shared lock."""
    global _tray_paused
    _tray_paused = paused
    _refresh_tray_status()


def _refresh_tray_status() -> None:
    """Refresh the tray image and title from recording/pause state."""
    global _tray_instance
    if _tray_instance is None:
        return
    try:
        _tray_instance.icon.icon = _create_icon(
            icon_path=_tray_instance._icon_path,
            recording=_tray_recording,
        )
        if _tray_recording:
            suffix = tr('tray.recording_suffix')
        elif _tray_paused:
            suffix = tr('tray.paused_suffix')
        else:
            suffix = ''
        _tray_instance.icon.title = f"{_tray_instance.display_title}{suffix}"
    except Exception as e:
        logger.warning(Notice('diagnostic.tray.failed_to_update_tray_icon_state', value0=e))


def refresh_language() -> None:
    """Menus resolve labels on opening; refresh the taskbar tooltip immediately."""
    _refresh_tray_status()


if __name__ == "__main__":
    from core.i18n import initialize_tool_language
    initialize_tool_language()
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    demo_icon_path = os.path.join(project_root, 'assets', 'client-icon.ico')
    enable_min_to_tray(icon_path=demo_icon_path)
    print(tr('terminal.tray.application_running_double_click_the_tray_icon_to'))
    while True:
        time.sleep(1)
