"""
Display management.

Decouple transcription from console output through a queue and display thread.
"""

import sys
import queue
import threading
from typing import Optional

class DisplayReporter:
    """Collect messages and print them on a background thread."""
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.message_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.current_segment = (0, 0) # (idx, total)
        self.skip_technical = False    # Skip intermediate technical records when enabled.
        self.thread.start()

    def print(self, message: str, force: bool = False):
        """Enqueue an ordinary message."""
        if not self.verbose:
            return
            
        # Build the prefix at submission time so asynchronous output preserves task context.
        prefix = ""
        if self.current_segment[1] > 1 and self.current_segment[0] > 0:
            prefix = f"[{self.current_segment[0]}/{self.current_segment[1]}] "
            
        if force or not self.skip_technical:
            self.message_queue.put(('print', (prefix, message)))

    def stream(self, chunk: str):
        """Enqueue a streamed text fragment."""
        if self.verbose:
            self.message_queue.put(('stream', chunk))

    def set_segment(self, current: int, total: int):
        """Set current segment metadata."""
        self.current_segment = (current, total)

    def _run(self):
        """Run the display loop."""
        last_was_stream = False
        while not (self.stop_event.is_set() and self.message_queue.empty()):
            try:
                msg_type, content = self.message_queue.get(timeout=0.1)
                
                if msg_type == 'print':
                    if last_was_stream:
                        sys.stdout.write("\n")
                        last_was_stream = False
                    
                    prefix, message = content
                    sys.stdout.write(f"{prefix}{message}\n")
                    sys.stdout.flush()
                
                elif msg_type == 'stream':
                    sys.stdout.write(content)
                    sys.stdout.flush()
                    last_was_stream = True
                
                self.message_queue.task_done()
            except queue.Empty:
                continue

    def stop(self):
        """Stop the display thread."""
        if self.thread.is_alive():
            self.stop_event.set()
            self.thread.join(timeout=1.0)
            # Flush the final display update.
            sys.stdout.write("\n")
            sys.stdout.flush()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
