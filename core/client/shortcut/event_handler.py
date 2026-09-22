# coding: utf-8
"""
Event handler.

Process keyboard and mouse events.
"""

from core.i18n import Notice

import time
from . import logger



class ShortcutEventHandler:
    """
    Shortcut event handler.

    Handle press/release transitions for starting, canceling, and finishing recording.
    """

    def __init__(self, tasks, pool, emulator):
        """
        Initialize the event handler.

        Args:
            tasks: Mapping of shortcut tasks.
            pool: Thread pool.
            emulator: Shortcut emulator.
        """
        self.tasks = tasks
        self.pool = pool
        self.emulator = emulator

    def handle_keydown(self, key_name, task) -> None:
        """Handle a key press."""
        # Hold mode.
        if task.shortcut.hold_mode:
            if not task.is_recording:
                task.launch()
            return

        # Toggle mode.
        if task.released:
            from threading import Event
            task.pressed = True
            task.released = False
            task.event = Event()  # Create a new event.
            self.pool.submit(self._count_down, task)
            self.pool.submit(self._manage_task, task)

    def handle_keyup(self, key_name, task) -> None:
        """Handle a key release."""
        # Toggle mode.
        if not task.shortcut.hold_mode:
            if task.pressed:
                task.pressed = False
                task.released = True
                task.event.set()
            return

        # Hold mode.
        if not task.is_recording:
            return

        duration = time.time() - task.recording_start_time
        logger.debug(Notice('diagnostic.event_handler.released_after_s', value0=key_name, value1=duration))

        if duration < task.threshold:
            self._handle_short_press(key_name, task)
        else:
            task.finish()

    def _handle_short_press(self, key_name, task) -> None:
        """Handle a short press."""
        cancel_start = time.perf_counter()
        task.cancel()
        cancel_time = (time.perf_counter() - cancel_start) * 1000
        logger.debug(Notice('diagnostic.event_handler.task_cancel_took_ms', value0=key_name, value1=cancel_time))

        if task.shortcut.suppress:
            logger.debug(Notice('diagnostic.event_handler.asynchronous_key_replay_scheduled', value0=key_name))
            self.pool.submit(self.emulator.emulate_key, key_name)

    def _count_down(self, task) -> None:
        """Count down to activation in toggle mode."""
        time.sleep(task.threshold)
        task.event.set()

    def _manage_task(self, task) -> None:
        """Manage recording in toggle mode."""
        was_recording = task.is_recording
        launched = True

        if not was_recording:
            launched = task.launch()

        if not launched:
            return

        if task.event.wait(timeout=task.threshold * 0.8):
            if task.is_recording and was_recording:
                task.finish()
        else:
            if not was_recording:
                task.cancel()
