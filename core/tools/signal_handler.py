
from core.i18n import tr
import signal
import sys
import time
import asyncio


class SignalHandler:
    def __init__(self, callback):
        self.last_time = time.time()
        self.callback = callback

    def __call__(self, signum, frame):
        now = time.time()
        if now - self.last_time > 1.0:
            self.last_time = now
            print(tr('terminal.signal_handler.received_press_again_within_one_second_to_exit', value0=signal.Signals(signum).name))
        else:
            print(tr('terminal.signal_handler.received_exiting', value0=signal.Signals(signum).name))
            self.last_time = 0
            self.callback()
            
def register_signal(callback):
    signal.signal(signal.SIGINT, SignalHandler(callback))