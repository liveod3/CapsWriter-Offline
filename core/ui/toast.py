"""
Toast notifications.

Display floating messages.

Usage:
    # Standard Toast.
    toast("Message", duration=3000)

    # Simulated streaming Toast for development.
    toast_stream("Message", markdown=False)
"""

from core.i18n import Notice, tr
import time
import logging
import threading
from typing import Union, Literal
import sys
import os

# Add the project root to sys.path when run directly.
if __name__ == "__main__":
    from core.i18n import initialize_tool_language
    initialize_tool_language()
    file_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(file_dir))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from core.ui.toast_manager import ToastMessageManager, ToastMessage
    from core.ui.toast_constants import (
        DEFAULT_DURATION_MS,
        DEFAULT_INITIAL_WIDTH,
        STREAM_CHAR_DELAY_S,
    )
else:
    from .toast_manager import ToastMessageManager, ToastMessage
    from .toast_constants import (
        DEFAULT_DURATION_MS,
        DEFAULT_INITIAL_WIDTH,
        STREAM_CHAR_DELAY_S,
    )


from . import logger


# ============================================================
# Public API.
# ============================================================

def toast(
    text: str,
    font_size: int = 14,
    bg: str = "#C41529",
    fg: str = 'white',
    duration: int = DEFAULT_DURATION_MS,
    initial_width: Union[float, int] = DEFAULT_INITIAL_WIDTH,
    initial_height: int = 0,
    streaming: bool = False,
    window_type: Literal['text', 'label'] = 'text',
    markdown: bool = False
) -> None:
    """Display a floating notification.

    Args:
        text: Message text.
        font_size: Font size.
        bg: Background color.
        fg: Text color.
        duration: Display duration in milliseconds.
        initial_width: Screen fraction for values 0-1; pixels for values above 1.
        initial_height: Initial height; 0 calculates it automatically.
        streaming: Enable streaming mode.
        window_type: 'text' or 'label'.
        markdown: Enable Markdown rendering.
    """
    manager = ToastMessageManager()
    msg = ToastMessage(
        text=text,
        font_size=font_size,
        bg=bg,
        fg=fg,
        duration=duration,
        initial_width=initial_width,
        initial_height=initial_height,
        streaming=streaming,
        window_type=window_type,
        markdown=markdown
    )
    manager.add_message(msg)


def toast_stream(
    text: str,
    font_size: int = 14,
    bg: str = "#C41529",
    fg: str = 'white',
    duration: int = DEFAULT_DURATION_MS,
    initial_width: Union[float, int] = DEFAULT_INITIAL_WIDTH,
    initial_height: int = 0,
    window_type: Literal['text', 'label'] = 'text',
    markdown: bool = False
) -> None:
    """Simulate streaming input for Toast development.

    Args:
        text: Message text.
        font_size: Font size.
        bg: Background color.
        fg: Text color.
        duration: Display duration in milliseconds.
        initial_width: Initial width.
        initial_height: Initial height.
        window_type: 'text' or 'label'.
        markdown: Enable Markdown rendering.
    """
    manager = ToastMessageManager()

    # Create a streaming Toast.
    msg = ToastMessage(
        text="",
        font_size=font_size,
        bg=bg,
        fg=fg,
        duration=duration,
        initial_width=initial_width,
        initial_height=initial_height,
        streaming=True,
        window_type=window_type,
        markdown=markdown
    )
    msg_id = manager.add_message(msg)

    # Simulate streamed output.
    def simulate_streaming():
        for i in range(len(text) + 1):
            if i > 0:
                manager.update_toast(msg_id, text[:i])
            time.sleep(STREAM_CHAR_DELAY_S)
        manager.finish_toast(msg_id)

    stream_thread = threading.Thread(
        target=simulate_streaming,
        daemon=True,
        name="StreamSimulationThread"
    )
    stream_thread.start()


# ============================================================
# Standalone development demo.
# ============================================================

if __name__ == "__main__":
    # Save demo logs beside the module.
    log_file = os.path.join(os.path.dirname(__file__), 'toast_debug.log')

    # Configure root logging for standalone execution.
    from core.ui.toast_logger import configure_toast_logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8', mode='w'),
            # logging.StreamHandler()  # Also write to the console.
        ],
        force=True  # Force logging reconfiguration.
    )

    logger.info(Notice('diagnostic.toast.log_file', value0=log_file))

    print("=" * 60)
    print(tr('terminal.toast.toast_test_program'))
    print("=" * 60)
    print(tr('terminal.toast.eight_test_cases'))
    print(tr('terminal.toast.text_plain_text_non_streaming'))
    print(tr('terminal.toast.text_plain_text_streaming'))
    print(tr('terminal.toast.text_markdown_non_streaming'))
    print(tr('terminal.toast.text_markdown_streaming'))
    print(tr('terminal.toast.label_plain_text_non_streaming'))
    print(tr('terminal.toast.label_plain_text_streaming'))
    print(tr('terminal.toast.label_markdown_non_streaming'))
    print(tr('terminal.toast.label_markdown_streaming'))
    print("=" * 60)

    # Sample text.
    plain_text = 5*"""在这个快节奏、信息爆炸的时代，我们似乎总是被一种无形的压力所裹挟，焦虑、烦恼、疲惫，像潮水般涌入我们的内心。我们争分夺秒地奔波于工作、学习、社交之间，却往往忽略了内心深处那片安静的土地。在这样的背景下，寻找静心，成为了我们重新审视自我、找回平衡的重要途径。"""

    markdown_text = """# Markdown 测试

## 功能特性

这是一段**粗体文字**和*斜体文字*的示例。

### 代码示例
```python
def hello():
    print("Hello, World!")
```

### 列表
- 第一项
- 第二项
- 第三项

> 这是一段引用文字"""*1

    # Text widget demo.

    # print("\n[Test 1] Text: plain, non-streaming (3 seconds)")
    # toast(plain_text, bg="#075077", fg='white', duration=3000, window_type='text', initial_width=800)
    # time.sleep(4)

    # print("[Test 2] Text: plain, streaming (5 seconds)")
    # toast_stream(plain_text, bg="#2E7D32", fg='white', duration=5000, window_type='text', initial_width=800, markdown=False)
    # time.sleep(7)

    # print("[Test 3] Text: Markdown, non-streaming (3 seconds)")
    # toast(markdown_text, bg="#1565C0", fg='white', duration=3000, window_type='text', initial_width=800, markdown=True)
    # time.sleep(4)

    # print("[Test 4] Text: Markdown, streaming (5 seconds)")
    # toast_stream(markdown_text, bg="#C62828", fg='white', duration=5000, window_type='text', initial_width=800, markdown=True)
    # time.sleep(7)

    # Label widget demo.

    # print("[Test 5] Label: plain, non-streaming (3 seconds)")
    # toast(plain_text, bg="#F57C00", fg='white', duration=3000, window_type='label', initial_width=800)
    # time.sleep(4)

    # print("[Test 6] Label: plain, streaming (5 seconds)")
    # toast_stream(plain_text, bg="#7B1FA2", fg='white', duration=5000, window_type='label', initial_width=800, markdown=False)
    # time.sleep(7)

    print(tr('terminal.toast.test_label_markdown_non_streaming_seconds'))
    toast(markdown_text, bg="#00796B", fg='white', duration=3000, window_type='label', initial_width=800, markdown=True)
    time.sleep(4)

    # print("[Test 8] Label: Markdown, streaming (5 seconds)")
    # toast_stream(markdown_text, bg="#5D4037", fg='white', duration=5000, window_type='label', initial_width=800, markdown=True)
    # time.sleep(7)

    print("\n" + "=" * 60)
    print(tr('terminal.toast.all_tests_complete_press_ctrl_c_to_exit'))
    print("=" * 60)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(tr('terminal.toast.exiting'))
