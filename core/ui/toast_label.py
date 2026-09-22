"""
Label-based Toast windows.

Display ordinary notifications with a Label widget.
"""

from core.i18n import Notice
import logging
import tkinter as tk
from tkinter import font
from typing import Optional, Callable, Union

from .toast_base import (
    ToastWindowBase,
    add_zero_width_for_chinese,
)
from .toast_constants import (
    DEFAULT_FONT_FAMILY,
    DEFAULT_PADDING_X,
    DEFAULT_PADDING_Y,
    MIN_WINDOW_HEIGHT,
    LABEL_HEIGHT_PADDING,
)
from .toast_logger import get_toast_logger

# Reuse application logging when available.
logger = get_toast_logger(__name__)


class ToastWindowLabel(ToastWindowBase):
    """Display floating messages with a Label widget.
    
    Support ordinary notifications and complete-text updates.
    
    Features:
        - Plain text display.
        - Automatic wrapping through wraplength.
        - Optional Markdown rendering.
    """

    def __init__(
        self,
        parent_root: tk.Tk,
        text: str,
        font_size: int = 14,
        font_family: str = '',
        bg: str = '#075077',
        fg: str = 'white',
        duration: int = 3000,
        initial_width: Union[float, int] = 400,
        initial_height: int = 0,
        streaming: bool = False,
        stop_callback: Optional[Callable[[], None]] = None,
        markdown: bool = False,
        editable: bool = False
    ) -> None:
        """Create a Label-based notification.
        
        Args:
            parent_root: Owning Tk root.
            text: Initial text.
            font_size: Font size in pixels.
            font_family: Font family; an empty string uses the default.
            bg: Background color.
            fg: Foreground text color.
            duration: Automatic close delay in milliseconds.
            initial_width: Screen fraction for values 0-1; pixels for values above 1.
            initial_height: Initial height; 0 calculates it automatically.
            streaming: Whether streaming output is enabled.
            stop_callback: Callback invoked when closing.
            markdown: Enable Markdown rendering.
            editable: Allow editing rendered Markdown.
        """
        # Initialize the base class.
        super().__init__(
            parent_root, text, font_size, font_family, bg, fg,
            duration, initial_width, initial_height, streaming,
            stop_callback, markdown, editable
        )

        # Compute the actual width.
        actual_width = self._calculate_actual_width()

        # Track incremental updates.
        self.last_char_count = 0

        # Add zero-width spaces after CJK characters for wrapping.
        processed_text = add_zero_width_for_chinese(text) if text else text

        # Use the default font when none is supplied.
        font_name = font_family if font_family else DEFAULT_FONT_FAMILY

        # Create the text label.
        self.label = tk.Label(
            self.window,
            text=processed_text,
            font=(font_name, font_size),
            fg=fg,
            bg=bg,
            justify=tk.LEFT,
            wraplength=actual_width - (DEFAULT_PADDING_X * 2),
            anchor='nw'
        )

        # Fill the window with fill=BOTH and expand=True.
        self.label.pack(
            side=tk.TOP,
            fill=tk.BOTH,
            expand=True,
            padx=DEFAULT_PADDING_X,
            pady=DEFAULT_PADDING_Y
        )

        # Initialize the character count.
        if text:
            self.last_char_count = len(text)

        # Recalculate layout.
        self.window.update_idletasks()

        # Set the initial position.
        self._set_window_position(initial=True)
        
        # Render Markdown immediately in non-streaming mode.
        if not streaming and markdown:
            self.window.update()
            self._switch_to_markdown()

    def _set_window_position(self, initial: bool = False) -> None:
        """Position the window.
        
        Args:
            initial: Use the initial centered position and single-line height.
        """
        try:
            screen_width = self.window.winfo_screenwidth()
            screen_height = self.window.winfo_screenheight()

            # Compute initial width.
            calculated_width = self._calculate_actual_width()

            # Get the Label's requested height.
            needed_h = self.label.winfo_reqheight() + LABEL_HEIGHT_PADDING

            # Respect a larger explicitly supplied height.
            if self.initial_height > 0:
                window_height = max(self.initial_height, needed_h)
            else:
                window_height = needed_h

            # Enforce minimum height.
            window_height = max(window_height, MIN_WINDOW_HEIGHT)
            window_width = calculated_width

            if initial:
                # Initially center horizontally with the top at the screen midpoint.
                x = (screen_width - window_width) // 2
                y = screen_height // 2
            else:
                # Resize without changing position.
                x = self.window.winfo_x()
                y = self.window.winfo_y()

            self.window.geometry(f'{window_width}x{window_height}+{x}+{y}')
        except tk.TclError as e:
            logger.warning(Notice('diagnostic.toast_label.window_positioning_failed', value0=e))

    def update_text(self, new_text: str) -> None:
        """Update the displayed text.
        
        Label replaces all text on each update; it does not support incremental insertion.
        
        Args:
            new_text: Complete replacement text.
        """
        if not self.streaming:
            return

        # Compute newly added characters.
        current_char_count = len(new_text)
        if current_char_count > self.last_char_count:
            # Retain the complete text.
            self.full_text = new_text

            # Add zero-width spaces after CJK characters for wrapping.
            processed_text = add_zero_width_for_chinese(new_text)

            try:
                # Replace all Label text.
                self.label.config(text=processed_text)

                # Recalculate layout synchronously.
                self.window.update_idletasks()

                # Compute the required height.
                needed_h = self.label.winfo_reqheight() + LABEL_HEIGHT_PADDING
                current_h = self.window.winfo_height()
                current_w = self.window.winfo_width()

                # Increase height if needed.
                if needed_h > current_h:
                    curr_x = self.window.winfo_x()
                    curr_y = self.window.winfo_y()
                    self.window.geometry(f"{current_w}x{int(needed_h)}+{curr_x}+{curr_y}")

                self.last_char_count = current_char_count
            except tk.TclError:
                # The window has been destroyed.
                self.streaming = False

    def _destroy_content_widget(self) -> None:
        """Destroy the Label widget."""
        if hasattr(self, 'label'):
            try:
                self.label.destroy()
            except tk.TclError:
                pass
