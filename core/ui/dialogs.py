# coding: utf-8
"""
Dialog utilities.

Provide shared dialog helpers and base types.
"""

from core.i18n import Notice, tr

import ctypes
import tkinter as tk
from tkinter import ttk
from typing import Optional, Callable

from . import logger

DEFAULT_FONT_FAMILY = 'Microsoft YaHei UI'

# Use the same process DPI awareness as the status overlay host.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except (OSError, AttributeError):
    pass


# ============================================================
# Utility functions.
# ============================================================

def create_modal_dialog(
    title: str,
    width: int = 600,
    height: int = 400,
    resizable: bool = False,
    withdraw: bool = True
) -> tk.Toplevel:
    """
    Create a modal dialog window.

    Args:
        title: Window title.
        width: Window width in pixels.
        height: Window height in pixels.
        resizable: Allow resizing.
        withdraw: Hide initially to avoid flicker; defaults to True.

    Returns:
        tkinter Toplevel instance.
    """
    # Create the Toplevel window.
    dialog = tk.Toplevel()
    dialog.title(title)

    # Hide initially to avoid flicker.
    if withdraw:
        dialog.withdraw()

    # Set window dimensions.
    dialog.geometry(f"{width}x{height}")
    dialog.resizable(resizable, resizable)

    # Configure modality.
    dialog.transient()  # Associate with the parent window.
    dialog.grab_set()   # Grab input within this application.

    # Center the dialog.
    _center_window(dialog, width, height)

    logger.debug(Notice('diagnostic.dialogs.creating_modal_dialog_x', value0=title, value1=width, value2=height))

    return dialog


def _center_window(window: tk.Toplevel, width: int, height: int) -> None:
    """
    Center a window on the screen.

    Args:
        window: Window instance.
        width: Window width.
        height: Window height.
    """
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()

    x = (screen_width - width) // 2
    y = (screen_height - height) // 2

    window.geometry(f"+{x}+{y}")


def create_label_button_frame(
    parent: tk.Widget,
    label_text: str,
    on_confirm: Callable[[], None],
    on_cancel: Callable[[], None],
    confirm_text: str | None = None,
    cancel_text: str | None = None
) -> ttk.Frame:
    """
    Create a standard button area.

    Args:
        parent: Parent container.
        label_text: Description text.
        on_confirm: Confirmation callback.
        on_cancel: Cancellation callback.
        confirm_text: Confirmation button label.
        cancel_text: Cancellation button label.

    Returns:
        Button container Frame.
    """
    frame = ttk.Frame(parent)
    frame.pack(pady=10)

    ttk.Button(frame, text=confirm_text if confirm_text is not None else tr('dialog.confirm'), command=on_confirm).pack(side="left", padx=5)
    ttk.Button(frame, text=cancel_text if cancel_text is not None else tr('dialog.cancel'), command=on_cancel).pack(side="left", padx=5)

    return frame


def create_scrolled_text(
    parent: tk.Widget,
    height: int = 5,
    font: tuple = (DEFAULT_FONT_FAMILY, 10)
) -> tk.Text:
    """
    Create a text widget with a scrollbar.

    Args:
        parent: Parent container.
        height: Text height in lines.
        font: Font settings.

    Returns:
        Text widget.
    """
    # Create the Text widget.
    text_widget = tk.Text(parent, height=height, font=font, wrap="word")

    # Add the scrollbar.
    scrollbar = ttk.Scrollbar(parent, command=text_widget.yview)
    text_widget.configure(yscrollcommand=scrollbar.set)

    return text_widget


def pack_scrolled_text(
    text_widget: tk.Text,
    scrollbar: ttk.Scrollbar,
    label_text: Optional[str] = None,
    parent: tk.Widget = None,
    padx: int = 10,
    pady_top: int = 10,
    pady_bottom: int = 5
) -> None:
    """
    Lay out a text widget and scrollbar.

    Args:
        text_widget: Text widget.
        scrollbar: Scrollbar widget.
        label_text: Optional label.
        parent: Container for the optional label.
        padx: Horizontal padding.
        pady_top: Top padding.
        pady_bottom: Bottom padding.
    """
    # Add the optional label.
    if label_text and parent:
        ttk.Label(parent, text=label_text).pack(anchor="w", padx=padx, pady=(pady_top, 0))

    # Lay out text and scrollbar.
    text_widget.pack(fill="both", expand=True, padx=padx, pady=(0 if label_text else pady_top, pady_bottom))
    scrollbar.pack(side="right", fill="y")


class DialogResult:
    """Dialog result container."""

    def __init__(self, confirmed: bool, **data):
        self.confirmed = confirmed  # Whether the user confirmed.
        self.data = data             # Returned dialog data.

    def __bool__(self) -> bool:
        """Return whether the user confirmed."""
        return self.confirmed

    def get(self, key: str, default=None):
        """Return dialog data."""
        return self.data.get(key, default)


def wait_window(dialog: tk.Toplevel) -> None:
    """
    Wait for a modal dialog to close.

    Args:
        dialog: Dialog window.
    """
    dialog.wait_window()
