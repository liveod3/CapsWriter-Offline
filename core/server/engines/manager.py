import queue
import time
import uuid
from types import SimpleNamespace

from core.server.schema import AlignRequest, AlignResponse
from .base import BaseAlignEngine
from . import logger
from ..delivery import ResultDeliveryError, positive_timeout


class ProcessAlignerProxy(BaseAlignEngine):
    """
    独立 Aligner 进程的同步代理。

    代理本身不导入也不持有 Aligner 模型；模型的加载、卸载和 GPU 后端
    生命周期全部位于兄弟进程中。
    """

    def __init__(self, queue_in, queue_out, timeout_sec=60, failure_event=None):
        self.queue_in = queue_in
        self.queue_out = queue_out
        self.timeout = positive_timeout(SimpleNamespace(timeout=timeout_sec), 'timeout', 60.0)
        self.failure_event = failure_event
        self._pending = {}

    def _failed(self, reason):
        if self.failure_event is not None:
            self.failure_event.set()
        raise ResultDeliveryError(reason)

    def align(self, audio, text, **kwargs):
        request_id = uuid.uuid4().hex
        task_id = str(kwargs.get('task_id', ''))
        request = AlignRequest(
            request_id=request_id,
            task_id=task_id,
            audio=audio,
            text=text,
            language=kwargs.get('language') or 'auto',
            offset_sec=float(kwargs.get('offset_sec', 0.0)),
        )

        try:
            self.queue_in.put(request, timeout=min(5.0, self.timeout))
        except (queue.Full, OSError, EOFError, ValueError):
            self._failed('AlignerSubmissionFailed')

        deadline = time.monotonic() + self.timeout
        while True:
            response = self._pending.pop(request_id, None)
            if response is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._failed('AlignerRequestTimeout')
                try:
                    response = self.queue_out.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    continue
                except (OSError, EOFError, ValueError):
                    self._failed('AlignerResultQueueFailed')

                if not isinstance(response, AlignResponse):
                    logger.warning(f"忽略未知 Aligner 响应: {type(response).__name__}")
                    continue
                if response.request_id != request_id:
                    self._pending[response.request_id] = response
                    if len(self._pending) > 32:
                        self._pending.pop(next(iter(self._pending)))
                    continue

            if response.error:
                logger.error(f"Aligner 对齐失败，任务 {task_id[:8]}: {response.error}")
                return None
            return response.result

    def check_idle(self):
        """兼容旧调用；闲置生命周期由独立进程管理。"""

    def cleanup(self):
        self._pending.clear()
