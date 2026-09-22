# coding: utf-8
"""
Recognition worker facade.

Coordinate model loading, signals, and task processing.
Own the recognition subprocess lifecycle.
"""

from core.i18n import Notice

import os
import sys
import signal
import atexit
from multiprocessing import Queue
from multiprocessing.managers import ListProxy
from platform import system

from .model_loader import ModelLoader
from .task_handler import TaskHandler
from ..state import WorkerState
from . import logger


class RecognizerWorker:
    """
    Recognition worker facade.
    
    Coordinate ModelLoader and TaskHandler for the subprocess lifetime.
    """
    def __init__(self, queue_in: Queue, queue_out: Queue, sockets_id: ListProxy,
                 align_queue_in: Queue, align_queue_out: Queue, stdin_fn: int = None,
                 failure_event=None, progress_clock=None):
        # 1. Initialize worker state.
        self.state = WorkerState()
        
        # 2. Inject state into core components.
        self.loader = ModelLoader(align_queue_in, align_queue_out, failure_event)
        self.handler = TaskHandler(queue_in, queue_out, sockets_id, self.state,
                                   failure_event, progress_clock)
        self.failure_event = failure_event
        
        # 3. Track lifecycle state.
        self.stdin_fn = stdin_fn
        self._is_running = False

    def _setup_environment(self):
        """Configure stdin, signals, and resource cleanup."""
        if self.stdin_fn is not None:
            try:
                sys.stdin = os.fdopen(self.stdin_fn)
            except Exception as e:
                logger.warning(Notice('diagnostic.worker.worker_cannot_take_over_standard_input', value0=str(e)))

        # Register graceful shutdown signal handlers.
        def signal_handler(signum, frame):
            # sig_name = signal.Signals(signum).name
            # logger.info(f"Worker received {sig_name} ({signum}); stopping")
            # self.stop()
            # exit(0)
            ...

        # Register primary signals only.
        signal.signal(signal.SIGINT, lambda signum, frame: None)
        
        # Register atexit fallback cleanup.
        atexit.register(self.stop)
        logger.debug(Notice('diagnostic.worker.worker_environment_configured'))

    def initialize(self):
        """Initialize the worker environment and load models."""
        # 1. Configure the environment.
        self._setup_environment()

        # 2. Load recognition models.
        logger.info(Notice('diagnostic.worker.worker_loading_speech_recognition_models'))
        self.loader.load()
        
        # 3. Pass loaded engines to the handler.
        self.handler.set_engine(
            recognizer=self.loader.recognizer, 
            punc_model=self.loader.punc_model,
            aligner=self.loader.aligner
        )
        
        # 4. Notify the parent that models are ready.
        self.handler.queue_out.put(True)
        
        # 5. Optionally trim the Windows working set.
        if system() == 'Windows':
            from core.tools.empty_working_set import empty_current_working_set
            empty_current_working_set()
        
        logger.info(Notice('diagnostic.worker.worker_resources_initialized'))

    def start(self):
        """
        Run the subprocess task loop.
        """
        if self._is_running:return
        self._is_running = True

        try:
            self.initialize()
            self.handler.loop()
        except Exception as exc:
            if self.failure_event is not None:
                self.failure_event.set()
            logger.error(Notice('diagnostic.worker.recognition_worker_stopped_error'), type(exc).__name__)
            raise
        finally:
            self.stop()


    def stop(self):
        """Stop the worker and release resources."""
        if not self._is_running:return
        self._is_running = False

        logger.info(Notice('diagnostic.worker.stopping_worker_and_releasing_resources'))
        self.loader.cleanup()
        logger.info(Notice('diagnostic.worker.worker_resources_released'))


    def run(self):
        """
        Multiprocessing entry point.
        """
        self.start()
