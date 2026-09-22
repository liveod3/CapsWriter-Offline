"""
Base Toast window.

Provide an abstract window and shared helpers.
"""

from core.i18n import Notice
import logging
import tkinter as tk
from tkinter import font
from typing import Optional, Callable, Union
from abc import ABC, abstractmethod
import ctypes

import markdown
from tkhtmlview import HTMLLabel

from .toast_constants import (
    DEFAULT_FONT_FAMILY,
    DEFAULT_PADDING_X,
    DEFAULT_PADDING_Y,
    MIN_WINDOW_HEIGHT,
    MARKDOWN_MIN_HEIGHT,
    SCROLL_STEP,
    DESTROY_DELAY_MS,
)
from . import logger

# Set DPI awareness once.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except (OSError, AttributeError):
    # Fallback for systems without this DPI API.
    pass


# ============================================================
# Utility functions.
# ============================================================

def add_zero_width_for_chinese(text: str) -> str:
    """Insert zero-width spaces after CJK characters for Label wrapping.
    
    This reduces uneven word-boundary wrapping in mixed-language text.
    
    Args:
        text: Original text.
        
    Returns:
        Text with zero-width spaces after CJK characters.
    """
    result = []
    for char in text:
        result.append(char)
        # Insert a zero-width space after CJK and full-width characters.
        if ord(char) > 127:
            result.append('\u200B')  # Zero-width space.
    return ''.join(result)


# ============================================================
# Abstract Toast window.
# ============================================================

class ToastWindowBase(ABC):
    """Base class for Toast windows.
    
    Handle window creation, dragging, mouse events, and timed destruction.
    Subclasses implement update_text and _destroy_content_widget.
    
    Attributes:
        window: tkinter Toplevel instance.
        streaming: Whether streaming output is enabled.
        markdown: Enable Markdown rendering.
        full_text: Complete message text.
    """

    def __init__(
        self,
        parent_root: tk.Tk,
        text: str,
        font_size: int,
        font_family: str,
        bg: str,
        fg: str,
        duration: int,
        initial_width: Union[float, int],
        initial_height: int,
        streaming: bool,
        stop_callback: Optional[Callable[[], None]],
        markdown_enabled: bool,
        editable: bool = False
    ) -> None:
        """Initialize the base Toast window.
        
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
            markdown_enabled: Enable Markdown rendering.
            editable: Allow editing rendered Markdown.
        """
        # Store basic properties.
        self.parent_root = parent_root
        self.stop_callback = stop_callback
        self.streaming = streaming
        self.markdown = markdown_enabled
        self.editable = editable
        self.duration = duration
        self.initial_width = initial_width
        self.initial_height = initial_height
        
        # State flags.
        self.pause = False
        self.mouse_inside = False
        self.timer_id: Optional[str] = None
        
        # Drag position.
        self.x = 0
        self.y = 0
        
        # Keep complete text and Markdown style settings.
        self.full_text = text
        self.font_size = font_size
        self.font_family = font_family if font_family else DEFAULT_FONT_FAMILY
        self.bg = bg
        self.fg = fg
        
        # Create the window.
        self.window = tk.Toplevel(parent_root)
        self.window.hang_on = False
        
        # Set window properties.
        self.window.overrideredirect(True)  # Use a borderless window.
        self.window.attributes('-topmost', True)  # Keep the window on top.
        self.window.configure(bg=bg)
        self.window.resizable(True, True)
        self.window.pack_propagate(False)
        
        # Bind shared events.
        self._bind_common_events()
        
        # Show the window.
        self.window.deiconify()
        
        # Schedule destruction for non-streaming output.
        if not self.streaming:
            self._start_destroy_timer()

    def _bind_common_events(self) -> None:
        """Bind dragging, hover, scrolling, Escape, and copy events."""
        self.window.bind('<ButtonPress-1>', self._on_drag_start)
        self.window.bind('<ButtonRelease-1>', self._on_drag_stop)
        self.window.bind('<B1-Motion>', self._on_drag_motion)
        self.window.bind('<Escape>', self._destroy_window)
        self.window.bind('<Enter>', self._on_mouse_enter)
        self.window.bind('<Leave>', self._on_mouse_leave)
        self.window.bind('<MouseWheel>', self._on_mouse_wheel)
        self.window.bind('<Button-4>', self._on_mouse_wheel)  # Linux scroll up.
        self.window.bind('<Button-5>', self._on_mouse_wheel)  # Linux scroll down.
        self.window.bind('<Control-c>', self._on_copy)        # Ctrl+C copies text.

    def _calculate_actual_width(self) -> int:
        """Calculate window width.
        
        Returns:
            Width in pixels.
        """
        screen_width = self.window.winfo_screenwidth()
        if 0 < self.initial_width < 1:
            # Interpret a fraction between 0 and 1 as a proportion of screen width.
            return int(screen_width * self.initial_width)
        else:
            # Interpret larger values as pixels.
            return int(self.initial_width)

    @staticmethod
    def _invert_color(hex_color: str) -> str:
        """Invert a hexadecimal RGB color.
        
        Args:
            hex_color: Hex color, such as '#075077'.
            
        Returns:
            Inverted hexadecimal color.
        """
        # Remove the # prefix.
        hex_color = hex_color.lstrip('#')
        # Parse RGB channels.
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        # Invert channels.
        inv_r = 255 - r
        inv_g = 255 - g
        inv_b = 255 - b
        return f"#{inv_r:02x}{inv_g:02x}{inv_b:02x}"

    # --------------------------------------------------------
    # Mouse event handlers.
    # --------------------------------------------------------

    def _on_mouse_enter(self, event: tk.Event) -> None:
        """Pause automatic closure while the pointer is over the window."""
        self.mouse_inside = True
        if self.timer_id:
            self.window.after_cancel(self.timer_id)
            self.timer_id = None

    def _on_mouse_leave(self, event: tk.Event) -> None:
        """Resume automatic closure when the pointer leaves."""
        self.mouse_inside = False
        if not self.streaming:
            self._start_destroy_timer()

    def _on_drag_start(self, event: tk.Event) -> None:
        """Start dragging."""
        self.pause = True
        self.x = event.x
        self.y = event.y

    def _on_drag_stop(self, event: tk.Event) -> None:
        """Finish dragging."""
        self.pause = False

    def _on_drag_motion(self, event: tk.Event) -> None:
        """Move the window while dragging."""
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.window.winfo_x() + deltax
        y = self.window.winfo_y() + deltay
        self.window.geometry(f"+{x}+{y}")

    def _on_mouse_wheel(self, event: tk.Event) -> str:
        """Move the window vertically with the mouse wheel.

        Bound movement between the screen midpoint and bottom:
        - Move up when the window extends below the screen.
        - Move down when the window extends above the midpoint.

        Returns:
            Return "break" to stop event propagation.
        """
        try:
            self.window.update_idletasks()
            current_y = self.window.winfo_y()
            window_height = self.window.winfo_height()
            screen_height = self.window.winfo_screenheight()
            screen_middle = screen_height // 2

            # Determine scroll direction.
            delta = getattr(event, 'delta', 0)
            num = getattr(event, 'num', 0)

            if delta:
                is_scroll_up = (delta < 0)
            elif num:
                is_scroll_up = (num != 4)
            else:
                return "break"

            # Compute movement bounds.
            # top_limit: Lowest window position, with its bottom at the screen bottom.
            # bottom_limit: Highest window position, with its top at the screen midpoint.
            top_limit = screen_height - window_height
            bottom_limit = screen_middle

            # Check whether movement is allowed.
            window_bottom = current_y + window_height
            can_scroll_up = window_bottom > screen_height  # Move up if the window extends below the screen.
            can_scroll_down = current_y < screen_middle    # Move down if the window extends above the midpoint.

            # Apply movement according to direction and current bounds.
            if is_scroll_up and can_scroll_up:
                # Move up by decreasing y.
                target_y = max(current_y - SCROLL_STEP, top_limit)
                if target_y != current_y:
                    self.window.geometry(f"+{self.window.winfo_x()}+{int(target_y)}")
            elif not is_scroll_up and can_scroll_down:
                # Move down by increasing y.
                target_y = min(current_y + SCROLL_STEP, bottom_limit)
                if target_y != current_y:
                    self.window.geometry(f"+{self.window.winfo_x()}+{int(target_y)}")

            return "break"
        except tk.TclError as e:
            logger.warning(Notice('diagnostic.toast_base.scroll_event_failed', value0=e))
            return "break"

    def _on_copy(self, _event: tk.Event) -> str:
        """Copy text to the clipboard.
        
        Prefer selected text; otherwise copy the complete message.

        Args:
            _event: Unused event object.

        Returns:
            Return "break" to stop event propagation.
        """
        try:
            text_to_copy = None
            
            # Check selection in md_label when present.
            if hasattr(self, 'md_label') and self.md_label:
                try:
                    # Text widgets expose selections through tag_ranges("sel").
                    sel_ranges = self.md_label.tag_ranges("sel")
                    if sel_ranges:
                        # Read selected text.
                        text_to_copy = self.md_label.get(sel_ranges[0], sel_ranges[1])
                        logger.info(Notice('diagnostic.toast_base.selected_text_copied_to_clipboard'))
                except tk.TclError:
                    pass  # No selection or selection lookup failed.
            
            # Copy the complete message if nothing is selected.
            if text_to_copy is None:
                text_to_copy = self.full_text
                logger.info(Notice('diagnostic.toast_base.all_toast_content_copied_to_clipboard'))
            
            self.window.clipboard_clear()
            self.window.clipboard_append(text_to_copy)
        except Exception as e:
            logger.error(Notice('diagnostic.toast_base.clipboard_copy_failed', value0=e))
        return "break"

    # --------------------------------------------------------
    # Window destruction.
    # --------------------------------------------------------

    def _start_destroy_timer(self) -> None:
        """Start the automatic destruction timer."""
        if self.timer_id:
            self.window.after_cancel(self.timer_id)
        self.timer_id = self.window.after(self.duration, self._destroy_window)

    def _destroy_window(self, event: Optional[tk.Event] = None) -> None:
        """Destroy the window.
        
        Args:
            event: Optional event from the Escape key.
        """
        try:
            # Invoke the stop callback, such as LLM cancellation.
            if self.stop_callback:
                try:
                    self.stop_callback()
                except Exception as e:
                    logger.warning(Notice('diagnostic.toast_base.stop_callback_failed', value0=e))

            if self.pause:
                # Delay destruction while dragging pauses the window.
                if self.timer_id:
                    self.window.after_cancel(self.timer_id)
                self.timer_id = self.window.after(DESTROY_DELAY_MS, self._destroy_window)
            else:
                if self.timer_id:
                    self.window.after_cancel(self.timer_id)
                    self.timer_id = None
                self.window.destroy()
        except tk.TclError:
            # The window may already be destroyed.
            pass

    # --------------------------------------------------------
    # Markdown rendering.
    # --------------------------------------------------------

    def _calculate_height_coefficient(self, content_height: int) -> float:
        """Compute a padding factor from content height.

        Use exponential decay: f(x) = 0.5 * e^(-0.003x) + 1.1.
        Short content needs a larger margin to remain fully visible.
        The factor approaches 1.1 as content height grows.

        Args:
            content_height: Content height in pixels.

        Returns:
            Padding factor in (1.1, 1.6].
            - x=50: f(50) ≈ 1.6
            - x=300: f(300) ≈ 1.3
            - x=1500: f(1500) ≈ 1.15
            - x→∞: f(x) → 1.1
        """
        import math

        if content_height <= 50:
            return 1.6

        # Exponential decay: f(x) = 0.5 * e^(-0.003x) + 1.1.
        coefficient = 0.5 * math.exp(-0.003 * content_height) + 1.1

        # Bound the factor.
        return max(coefficient, 1.1)

    def _switch_to_markdown(self) -> None:
        """Switch the content widget to Markdown rendering."""
        try:
            # Save the current position.
            cx, cy = self.window.winfo_x(), self.window.winfo_y()

            # Convert Markdown to HTML.
            raw_html = markdown.markdown(
                self.full_text,
                extensions=['extra', 'nl2br']
            )

            # Wrap in HTML without padding; HTMLLabel owns padding.
            full_html = f"""
            <div style="background-color:{self.bg}; color:{self.fg};
                        font-family:{self.font_family}; font-size:{self.font_size}px;">
                {raw_html}
            </div>
            """

            # Overlay HTMLLabel before removing the original widget.
            self.md_label = HTMLLabel(
                self.window,
                html=full_html,
                wrap="char",
                background=self.bg,
                padx=DEFAULT_PADDING_X,
                pady=DEFAULT_PADDING_Y
            )
            self.md_label.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            
            # Apply the editable option.
            # DISABLED permits selection but prevents editing.
            # NORMAL permits selection and editing.
            if self.editable:
                self.md_label.config(state=tk.NORMAL)
                
                # Invert the background color for the caret.
                cursor_color = self._invert_color(self.bg)
                self.md_label.config(insertbackground=cursor_color, insertwidth=2)
            # Otherwise retain HTMLLabel's default DISABLED state.
            
            # Make selection visible even in DISABLED state.
            select_bg = "#3399ff"
            select_fg = "white"
            self.md_label.config(selectbackground=select_bg, selectforeground=select_fg)
            
            # Apply selection styling to HTMLLabel's text tags too.
            for tag in self.md_label.tag_names():
                self.md_label.tag_config(tag, selectbackground=select_bg, selectforeground=select_fg)

            # Destroy the old widget after the Markdown widget is visible.
            self._destroy_content_widget()

            # Update layout until dimensions are available.
            self.window.update()
            self.window.update_idletasks()

            # Use fit_height() to measure content height.
            # Fit the label height to all content.
            self.md_label.fit_height()
            self.window.update()
            self.window.update_idletasks()

            # Measure after fit_height().
            height_after = self.md_label.winfo_height()
            reqheight_after = self.md_label.winfo_reqheight()

            # Use the larger measured/requested height.
            content_height = max(height_after, reqheight_after)

            # Compute new window dimensions.
            # Apply dynamic padding to avoid clipped content.
            margin_coefficient = self._calculate_height_coefficient(content_height)
            final_h = max(int(content_height * margin_coefficient), MARKDOWN_MIN_HEIGHT)
            final_w = self._calculate_actual_width()

            self.window.geometry(f"{final_w}x{int(final_h)}+{cx}+{cy}")

            # Measure again after updating layout.
            self.window.update()
            self.window.update_idletasks()

            logger.info(Notice('diagnostic.toast_base.markdown_window_x_content_px_margin', value0=final_w, value1=final_h, value2=content_height, value3=margin_coefficient))

        except Exception as e:
            logger.error(Notice('diagnostic.toast_base.markdown_conversion_failed', value0=e))
            self._destroy_window()

    # --------------------------------------------------------
    # Subclass interface.
    # --------------------------------------------------------

    @abstractmethod
    def update_text(self, new_text: str) -> None:
        """Update displayed text; implemented by subclasses.
        
        Args:
            new_text: Complete replacement text.
        """
        pass

    @abstractmethod
    def _destroy_content_widget(self) -> None:
        """Destroy the content widget; implemented by subclasses."""
        pass

    # --------------------------------------------------------
    # Streaming completion.
    # --------------------------------------------------------

    def finish(self) -> None:
        """Finish streamed output.

        Mark streaming complete and render Markdown when enabled,
        then start the automatic destruction timer.
        """
        if self.streaming:
            self.streaming = False

            # Render Markdown when enabled.
            if self.markdown:
                self._switch_to_markdown()

            # Start the timer only when the pointer is outside the window.
            if not self.mouse_inside:
                self._start_destroy_timer()
