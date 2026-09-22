"""
Block the forward mouse button and Caps Lock events.

Use pynput listeners to suppress:
1. The forward mouse button (X2 / Button.x2).
2. The Caps Lock key.

Prevent accidental actions caused by these buttons.

Run:
    python block_mouse_forward.py

Exit:
    Press Ctrl+C to stop the program.

Install dependencies:
    pip install pynput

Implementation notes:
    - Use win32_event_filter to suppress selected events.
    - Mouse: Button.x1 is Back; Button.x2 is Forward.
    - Keyboard: the Caps Lock virtual-key code is 0x14.
"""

from pynput import mouse, keyboard
import sys


class InputBlocker:
    """Suppress selected input events."""

    # Windows mouse message constants.
    WM_XBUTTONDOWN = 0x020B
    WM_XBUTTONUP = 0x020C
    WM_XBUTTONDBLCLK = 0x020D

    # XBUTTON identifiers.
    XBUTTON1 = 0x0001  # Back button.
    XBUTTON2 = 0x0002  # Forward button.

    # Windows virtual-key codes.
    VK_CAPITAL = 0x14  # Caps Lock

    def __init__(self):
        """Initialize the input blocker."""
        self.mouse_listener = None
        self.keyboard_listener = None

        # Event counters.
        self.mouse_forward_blocked = 0
        self.mouse_back_detected = 0
        self.capslock_blocked = 0

    def create_mouse_filter(self):
        """
        Create the Windows mouse event filter.

        Returns:
            callable: Mouse event filter.
        """
        def win32_event_filter(msg, data):
            """
            Filter mouse events.

            Args:
                msg: Windows message type.
                data: MSLLHOOKSTRUCT containing the mouse data.

            Returns:
                bool: False hides the event from the listener.
            """
            # Check for an XBUTTON message.
            if msg in (self.WM_XBUTTONDOWN, self.WM_XBUTTONUP, self.WM_XBUTTONDBLCLK):
                # Extract the XBUTTON identifier from the high word.
                xbutton = (data.mouseData >> 16) & 0xFFFF

                if xbutton == self.XBUTTON2:
                    # Suppress the forward button (X2).
                    if msg == self.WM_XBUTTONDOWN:
                        self.mouse_forward_blocked += 1
                        print(f"🚫 Blocked Forward press #{self.mouse_forward_blocked}")
                    elif msg == self.WM_XBUTTONUP:
                        print(f"🚫 Blocked Forward release #{self.mouse_forward_blocked}")
                    # Call suppress_event() to prevent delivery to the system.
                    self.mouse_listener.suppress_event()
                    return False

                elif xbutton == self.XBUTTON1:
                    # Record the back button (X1) without suppressing it.
                    if msg == self.WM_XBUTTONDOWN:
                        self.mouse_back_detected += 1
                        print(f"⚠️  Observed Back press #{self.mouse_back_detected}")
                    elif msg == self.WM_XBUTTONUP:
                        print(f"⚠️  Observed Back release #{self.mouse_back_detected}")

            return True

        return win32_event_filter

    def create_keyboard_filter(self):
        """
        Create the Windows keyboard event filter.

        Returns:
            callable: Keyboard event filter.
        """
        def win32_event_filter(msg, data):
            """
            Filter keyboard events.

            Args:
                msg: Windows message type; this filter selects events by vkCode.
                data: KBDLLHOOKSTRUCT containing the keyboard data.

            Returns:
                bool: False hides the event from the listener.
            """
            # Identify Caps Lock by its virtual-key code.
            if data.vkCode == self.VK_CAPITAL:
                # Suppress Caps Lock.
                self.capslock_blocked += 1
                # Distinguish press and release messages.
                if msg == 0x0100:  # WM_KEYDOWN
                    print(f"🚫 Blocked Caps Lock press #{self.capslock_blocked}")
                elif msg == 0x0101:  # WM_KEYUP
                    print(f"🚫 Blocked Caps Lock release #{self.capslock_blocked}")
                # Call suppress_event() to prevent delivery to the system.
                self.keyboard_listener.suppress_event()
                return False

            return True

        return win32_event_filter

    def on_mouse_click(self, _x, _y, _button, _pressed):
        """
        Handle mouse clicks; the filter performs all processing.

        Args:
            _x: Mouse X coordinate (unused).
            _y: Mouse Y coordinate (unused).
            _button: Button type (unused).
            _pressed: Whether the button is pressed (unused).

        Returns:
            bool: False stops the listener.
        """
        # Mark these parameters as intentionally unused.
        _ = _x, _y, _button, _pressed
        # Process events in win32_event_filter without displaying other keys.
        return True

    def on_key_press(self, _key):
        """
        Handle key presses; the filter performs all processing.

        Args:
            _key: Pressed key (unused).

        Returns:
            bool: False stops the listener.
        """
        # Mark these parameters as intentionally unused.
        _ = _key
        # Process events in win32_event_filter without displaying other keys.
        return True

    def start(self):
        """Start the listener."""
        print("=" * 60)
        print("🛡️  Input blocker started")
        print("=" * 60)
        print("✅ Forward mouse button (X2) is suppressed")
        print("✅ Caps Lock is suppressed")
        print("⚠️  Back mouse button (X1) is recorded without suppression")
        print("📝 Press Ctrl+C to exit")
        print("=" * 60)
        print()

        # Create the mouse listener.
        self.mouse_listener = mouse.Listener(
            on_click=self.on_mouse_click,
            win32_event_filter=self.create_mouse_filter()
        )

        # Create the keyboard listener.
        self.keyboard_listener = keyboard.Listener(
            on_press=self.on_key_press,
            win32_event_filter=self.create_keyboard_filter()
        )

        # Start both listeners.
        self.mouse_listener.start()
        self.keyboard_listener.start()

        try:
            # Keep the program running.
            self.mouse_listener.join()
            self.keyboard_listener.join()
        except KeyboardInterrupt:
            print("\n" + "=" * 60)
            print("👋 Program exited")
            print(f"   Blocked Forward events: {self.mouse_forward_blocked} times")
            print(f"   Observed Back events: {self.mouse_back_detected} times")
            print(f"   Blocked Caps Lock events: {self.capslock_blocked} times")
            print("=" * 60)
        finally:
            self.stop()

    def stop(self):
        """Stop the listener."""
        if self.mouse_listener and self.mouse_listener.running:
            self.mouse_listener.stop()
        if self.keyboard_listener and self.keyboard_listener.running:
            self.keyboard_listener.stop()
        print("🛑 Listeners stopped")


def main():
    """Run the entry point."""
    blocker = InputBlocker()

    # Use UTF-8 for the Windows console.
    if sys.platform == "win32":
        import codecs

        # Support Unicode output in the Windows console.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            # Fallback for Python versions before 3.7.
            sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")

    blocker.start()


if __name__ == "__main__":
    main()
