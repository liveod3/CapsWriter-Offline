"""
Text-based Toast windows.

Use a Text widget for incremental streaming display.
"""

from core.i18n import Notice
import logging
import tkinter as tk
from tkinter import font
from typing import Optional, Callable, Union

from .toast_base import (
    ToastWindowBase,
)
from .toast_constants import (
    DEFAULT_FONT_FAMILY,
    DEFAULT_PADDING_X,
    DEFAULT_PADDING_Y,
    MIN_WINDOW_HEIGHT,
    STREAMING_TEXT_HEIGHT,
    NON_STREAMING_TEXT_HEIGHT,
    HEIGHT_PADDING,
    TEXT_WRAP_MODE,
)
from .toast_logger import get_toast_logger

# Reuse application logging when available.
logger = get_toast_logger(__name__)


class ToastWindowText(ToastWindowBase):
    """Display floating messages with a Text widget.
    
    Append streamed text and grow the window to fit.
    
    Features:
        - Incremental text output.
        - Automatic height adjustment.
        - Markdown conversion after streaming completes.
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
        """Create a Text-based notification.
        
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

        # Create a font object to measure line height.
        font_name = self.font_family if self.font_family else DEFAULT_FONT_FAMILY
        self.my_font = font.Font(family=font_name, size=self.font_size)
        self.line_height = self.my_font.metrics('linespace')
        self.last_char_count = 0

        # Create the Text widget.
        text_height = STREAMING_TEXT_HEIGHT if streaming else NON_STREAMING_TEXT_HEIGHT
        
        self.text_area = tk.Text(
            self.window,
            font=self.my_font,
            fg=self.fg,
            bg=self.bg,
            padx=DEFAULT_PADDING_X,
            pady=DEFAULT_PADDING_Y,
            borderwidth=0,
            highlightthickness=0,
            wrap=TEXT_WRAP_MODE,
            insertofftime=0,
            state=tk.DISABLED,
            cursor="arrow",
            height=text_height
        )

        # Anchor at the top left and fill the window.
        self.text_area.pack(side=tk.TOP, anchor='nw', fill=tk.BOTH, expand=True)

        # Replace Text's built-in wheel scrolling with window movement.
        # Bind a no-op to bypass widget scrolling while allowing propagation.
        def pass_to_window(event):
            # Let the window handle the event.
            self.window.event_generate('<MouseWheel>', x=event.x, y=event.y, delta=event.delta)
            return "break"

        self.text_area.bind('<MouseWheel>', pass_to_window)
        self.text_area.bind('<Button-4>', pass_to_window)  # Linux
        self.text_area.bind('<Button-5>', pass_to_window)  # Linux

        # Insert initial text.
        if text:
            self.text_area.config(state=tk.NORMAL)
            self.text_area.insert(tk.END, text)
            self.text_area.config(state=tk.DISABLED)
            self.last_char_count = len(text)

        # Recalculate layout.
        self.window.update_idletasks()

        # In non-streaming mode, set width before measuring wrapped lines.
        if not streaming and text:
            # Apply width using a temporary single-line height.
            calculated_width = self._calculate_actual_width()
            screen_width = self.window.winfo_screenwidth()
            screen_height = self.window.winfo_screenheight()
            temp_x = (screen_width - calculated_width) // 2
            temp_y = screen_height // 2
            self.window.geometry(f'{calculated_width}x100+{temp_x}+{temp_y}')

            # Update the layout so wrapping uses the actual width.
            self.window.update_idletasks()

            # Count rendered lines.
            result = self.text_area.count('1.0', 'end', 'displaylines')
            actual_lines = result[0] if result else 1
            self.text_area.config(height=actual_lines)

        # Set the initial position using the measured line count.
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

            # Update the window before reading dimensions.
            self.window.update_idletasks()

            # Use the base width calculation.
            calculated_width = self._calculate_actual_width()

            # Read the rendered line count.
            result = self.text_area.count('1.0', 'end', 'displaylines')
            current_lines = result[0] if result else 1

            # Height is line count times line height plus vertical padding.
            needed_h = (current_lines * self.line_height) + HEIGHT_PADDING

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

            self.window.geometry(f'{window_width}x{int(window_height)}+{x}+{y}')
        except tk.TclError as e:
            logger.warning(Notice('diagnostic.toast_label.window_positioning_failed', value0=e))

    def update_text(self, new_text: str) -> None:
        """Append newly streamed text.
        
        In streaming mode, append new characters to the Text widget
        and resize the window to fit the content.
        
        Args:
            new_text: Complete updated text.
        """
        if not self.streaming:
            return

        # Check that the window still exists.
        try:
            if not self.window.winfo_exists():
                self.streaming = False
                return
        except tk.TclError:
            self.streaming = False
            return

        # Compute newly added characters.
        current_char_count = len(new_text)
        if current_char_count > self.last_char_count:
            # Retain the complete text.
            self.full_text = new_text
            
            # Extract newly added text.
            new_chars = new_text[self.last_char_count:]

            try:
                # Update the Text widget.
                self.text_area.config(state=tk.NORMAL)
                self.text_area.insert(tk.END, new_chars)
                self.text_area.config(state=tk.DISABLED)

                # Recalculate layout synchronously.
                self.window.update_idletasks()

                # Compute required height.
                result = self.text_area.count('1.0', 'end', 'displaylines')
                current_lines = result[0] if result else 1
                needed_h = (current_lines * self.line_height) + HEIGHT_PADDING
                current_h = self.window.winfo_height()
                current_w = self.window.winfo_width()

                # Resize the Text widget to show all lines.
                self.text_area.config(height=current_lines)

                # Increase height if needed.
                if needed_h > current_h:
                    curr_x = self.window.winfo_x()
                    curr_y = self.window.winfo_y()
                    self.window.geometry(f"{current_w}x{int(needed_h)}+{curr_x}+{curr_y}")

                self.last_char_count = current_char_count
            except tk.TclError:
                # Stop streaming if the window has been destroyed.
                self.streaming = False

    def _destroy_content_widget(self) -> None:
        """Destroy the Text widget."""
        if hasattr(self, 'text_area'):
            try:
                self.text_area.destroy()
            except tk.TclError:
                pass
