# coding: utf-8
"""
识别子进程工作包 (Worker Package)

包含模型加载、任务处理和 Worker 门面类。
"""

from multiprocessing import Queue
from multiprocessing.managers import ListProxy
from .. import logger
from .worker import RecognizerWorker

def start_worker(queue_in: Queue, queue_out: Queue, sockets_id: ListProxy,
                 align_queue_in: Queue, align_queue_out: Queue, stdin_fn: int,
                 failure_event=None, progress_clock=None):
    """识别子进程启动入口"""
    worker = RecognizerWorker(
        queue_in, queue_out, sockets_id,
        align_queue_in, align_queue_out, stdin_fn, failure_event, progress_clock,
    )
    worker.run()

__all__ = ['RecognizerWorker', 'start_worker']
