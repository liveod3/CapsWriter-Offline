
import threading
import queue
import weakref
from concurrent.futures import Future, ThreadPoolExecutor

class SimpleDaemonExecutor(ThreadPoolExecutor):
    """
    Create one daemon thread per submitted task.
    This executor does not pool threads.
    Use it for a small number of blocking I/O tasks, such as queue consumers.
    Threads retain daemon shutdown behavior.
    """
    def submit(self, fn, *args, **kwargs):
        f = Future()
        
        def wrapper():
            # A running queue read cannot be canceled by canceling its async waiter.
            if not f.set_running_or_notify_cancel():
                return
            try:
                result = fn(*args, **kwargs)
                f.set_result(result)
            except Exception as e:
                f.set_exception(e)

        t = threading.Thread(target=wrapper, daemon=True)
        t.start()
        return f
    
    def shutdown(self, wait=True):
        pass
