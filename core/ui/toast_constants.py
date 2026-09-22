"""
Toast constants.

Define shared Toast window constants.
"""
import tkinter as tk

# ============================================================
# Fonts and styles.
# ============================================================

DEFAULT_FONT_FAMILY = 'Microsoft YaHei UI'
DEFAULT_PADDING_X = 20
DEFAULT_PADDING_Y = 15

# ============================================================
# Window dimensions.
# ============================================================

MIN_WINDOW_HEIGHT = 60  # Minimum window height.

# Text widget settings.
STREAMING_TEXT_HEIGHT = 5  # Start streaming with few lines to avoid an oversized empty window.
NON_STREAMING_TEXT_HEIGHT = 1  # Initial non-streaming line count.
HEIGHT_PADDING = 40  # Additional vertical padding for Text windows.
TEXT_WRAP_MODE = tk.CHAR  # Text wrapping: CHAR wraps characters; WORD wraps words.

# Label widget settings.
LABEL_HEIGHT_PADDING = 30  # Additional vertical padding for Label windows.

# Markdown rendering settings.
MARKDOWN_MIN_HEIGHT = 50  # Minimum Markdown window height.

# ============================================================
# Interaction settings.
# ============================================================

SCROLL_STEP = 60  # Pixels moved per wheel step.
DESTROY_DELAY_MS = 100  # Destruction delay while dragging, in milliseconds.

# ============================================================
# Defaults.
# ============================================================

DEFAULT_DURATION_MS = 2000  # Default display duration.
DEFAULT_INITIAL_WIDTH = 0.5  # Default width as half the screen.

# ============================================================
# Internal settings.
# ============================================================

QUEUE_POLL_INTERVAL_MS = 100  # Queue polling interval in milliseconds.
STREAM_CHAR_DELAY_S = 0.0001  # Simulated streaming delay per character, in seconds.
TK_SCALING_FACTOR = 2  # Tk scaling factor for high-DPI displays.
