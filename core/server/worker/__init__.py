# coding: utf-8
"""
Recognition worker package.

Load models, process tasks, and expose the worker facade.
"""

from core.i18n import Notice, set_language

from multiprocessing import Queue
from multiprocessing.managers import ListProxy
from .. import logger
from .worker import RecognizerWorker

def start_worker(queue_in: Queue, queue_out: Queue, sockets_id: ListProxy,
                 align_queue_in: Queue, align_queue_out: Queue, stdin_fn: int,
                 failure_event=None, progress_clock=None):
    """Start the recognition subprocess."""
    from config_server import ServerConfig
    set_language(getattr(ServerConfig, 'ui_language', 'auto'))
    try:
        worker = RecognizerWorker(
            queue_in, queue_out, sockets_id,
            align_queue_in, align_queue_out, stdin_fn, failure_event, progress_clock,
        )
        worker.run()
    except Exception as exc:
        if failure_event is not None:
            failure_event.set()
        logger.error(Notice('diagnostic.__init__.recognition_process_exited_error'), type(exc).__name__)
        raise SystemExit(1) from None

__all__ = ['RecognizerWorker', 'start_worker']
