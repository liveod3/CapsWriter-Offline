# coding: utf-8
"""Process queued audio and return recognition results to the main process.

Schedule (socket_id, task_id) pairs round-robin, preserving FIFO within each pair.
"""

from collections import OrderedDict, deque
from multiprocessing import Queue
from multiprocessing.managers import ListProxy
import queue
import time
from config_server import ServerConfig as Config
from .pipeline import TaskPipeline
from ..state import WorkerState
from ..schema import Result, TaskKey
from ..delivery import ResultDeliveryError, positive_timeout
from .gpu_boost import GpuBoostManager
from .gpu_monitor import GpuMemoryMonitor
from . import logger
from .supervision import progress


class TaskBuffer:
    """Buffer connection-scoped tasks for round-robin scheduling."""
    def __init__(self, state: WorkerState):
        self.state = state
        self._buffers: OrderedDict[TaskKey, deque] = OrderedDict()

    def enqueue(self, task):
        """Append a fragment and ensure its owning session exists."""
        if self.state.failed_tasks.contains(task.socket_id, task.task_id):
            return
        key = task.key
        if key not in self._buffers:
            self._buffers[key] = deque()
            self.state.get_session(task.task_id, task.socket_id, task.type)
        self._buffers[key].append(task)

    def pop(self):
        """轮转取出一个 session 的下一个任务。没有待处理任务时返回 None。"""
        if not self._buffers:
            return None

        key, buf = next(iter(self._buffers.items()))
        task = buf.popleft()

        if buf:
            self._buffers.move_to_end(key)
        else:
            del self._buffers[key]

        return task

    def cleanup_tasks(self):
        """Remove buffered fragments whose owning sessions were removed."""
        for key in list(self._buffers):
            if key not in self.state.sessions:
                logger.debug(
                    f"Removed buffered session: socket={key[0][:8]}, task={key[1][:8]}"
                )
                del self._buffers[key]

    @property
    def is_empty(self) -> bool:
        return len(self._buffers) == 0

    @property
    def task_count(self) -> int:
        """当前进程内尚未处理的任务片段总数。"""
        return sum(len(buffer) for buffer in self._buffers.values())


class TaskHandler:
    """
    任务处理器

    协调输入输出队列与识别引擎之间的任务流。
    支持跨 task 公平轮转调度。
    """
    def __init__(self, queue_in: Queue, queue_out: Queue, sockets_id: ListProxy, state: WorkerState,
                 failure_event=None, progress_clock=None):
        self.queue_in = queue_in
        self.queue_out = queue_out
        self.sockets_id = sockets_id
        self.state = state
        self.failure_event = failure_event
        self.progress_clock = progress_clock
        self.session_activity = {}
        self.session_timeout = positive_timeout(Config, 'worker_stall_timeout', 600.0)
        self.result_queue_timeout = positive_timeout(Config, 'result_queue_timeout', 60.0)

        self.recognizer = None
        self.punc_model = None
        self.aligner = None
        self.pipeline = None

        self.buffer = TaskBuffer(state)
        try:
            self.max_buffer_tasks = max(
                1,
                int(getattr(Config, 'worker_buffer_max_tasks', 64)),
            )
        except (TypeError, ValueError):
            self.max_buffer_tasks = 64
        self.gpu_boost = GpuBoostManager(state)
        self.gpu_monitor = GpuMemoryMonitor(
            console=self._console,
            enabled=getattr(Config, 'gpu_memory_warning_enabled', True),
            interval=getattr(Config, 'gpu_memory_warning_interval', 1.0),
            threshold=getattr(Config, 'gpu_memory_warning_threshold', 0.90),
            consecutive_samples=getattr(
                Config,
                'gpu_memory_warning_consecutive_samples',
                3,
            ),
        )

    @property
    def _console(self):
        # 延迟导入，避免模块初始化阶段引入额外的服务端状态依赖。
        from ..state import console
        return console

    def set_engine(self, recognizer, punc_model=None, aligner=None):
        """注入识别引擎实例并初始化管线"""
        self.recognizer = recognizer
        self.punc_model = punc_model
        self.aligner = aligner
        self.pipeline = TaskPipeline(recognizer, punc_model, aligner, self.state)

    def drain_queue(self) -> bool:
        """Drain 队列中所有任务到缓冲区。Returns: False = 退出信号。"""
        while True:
            progress(self.progress_clock, update=True)
            # 多进程队列虽有界，但持续 drain 会把压力转移到本进程内存；
            # 达到上限后先处理一个片段，再继续接收。
            if self.buffer.task_count >= self.max_buffer_tasks:
                return True

            # 获取任务
            try:
                if self.buffer.is_empty:
                    task = self.queue_in.get(timeout=1)
                else:
                    task = self.queue_in.get(timeout=0.02)
            except queue.Empty:
                self.cleanup()
                if self.buffer.is_empty:
                    self.cleanup_engines()
                    continue
                else:
                    return True
            except (OSError, EOFError, ValueError) as exc:
                raise ResultDeliveryError('InputQueueFailed') from exc
            
            # 判断退出信号
            if task is None:
                return False

            # 跳过已断开连接客户端的任务
            if task.socket_id not in self.sockets_id:
                logger.debug(f"跳过断连客户端任务: {task.task_id[:8]}")
                continue

            if task.type == 'cmd' and task.command == 'cancel':
                self.state.failed_tasks.add(task.socket_id, task.task_id)
                self.state.sessions.pop(task.key, None)
                self.buffer.cleanup_tasks()
                continue

            # 任务进入缓冲区
            self.buffer.enqueue(task)
            if task.key in self.state.sessions:
                self.session_activity[task.key] = time.monotonic()

    def cleanup(self):
        """清理断连 socket 的缓冲任务和 session。"""
        self.state.cleanup_sessions(self.sockets_id)
        self.state.failed_tasks.retain(self.sockets_id)
        for key in list(self.state.sessions):
            if self.state.failed_tasks.contains(*key):
                self.state.sessions.pop(key, None)
        self.buffer.cleanup_tasks()
        self.session_activity = {key: last for key, last in self.session_activity.items()
                                 if key in self.state.sessions}
        if any(time.monotonic() - last >= self.session_timeout
               for last in self.session_activity.values()):
            # A silently lost final/cancel must not leave an immortal worker session.
            raise ResultDeliveryError('TaskProgressTimeout')

    def cleanup_engines(self):
        """闲置资源清理：对齐器卸载 + GPU 加速取消。"""
        if self.pipeline and self.pipeline.aligner:
            self.pipeline.aligner.check_idle()
        self.gpu_boost.check_idle()

    def handle_command_task(self, task):
        """处理命令任务。"""
        try:
            self.gpu_boost.handle_command(task)
        finally:
            self.state.sessions.pop(task.key, None)

    def handle_audio_task(self, task):
        """处理音频识别任务。"""
        if self.state.failed_tasks.contains(task.socket_id, task.task_id):
            return
        try:
            self.gpu_monitor.begin_task()
            try:
                result = self.pipeline.process(task)
            finally:
                self.gpu_monitor.end_task()
        except ResultDeliveryError:
            raise
        except Exception as exc:
            close_connection = self.state.failed_tasks.add(task.socket_id, task.task_id)
            self.state.sessions.pop(task.key, None)
            self.cleanup()
            result = Result(
                task_id=task.task_id, socket_id=task.socket_id, type=task.type,
                is_final=True, error_code='recognition_failed',
                supports_task_errors=task.supports_task_errors,
                close_connection=close_connection,
                time_start=task.time_start, time_submit=task.time_submit,
                time_complete=time.time(),
            )
            logger.error('Recognition task failed: socket=%s task=%s error=%s',
                         task.socket_id[:8], task.task_id[:8], type(exc).__name__)
        deadline = time.monotonic() + self.result_queue_timeout
        while task.socket_id in self.sockets_id:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResultDeliveryError('OutputQueueTimeout')
            try:
                self.queue_out.put(result, timeout=min(0.5, remaining))
                break
            except queue.Full:
                # Backpressure is temporary; permanent blockage is terminal.
                continue
            except (OSError, EOFError, ValueError) as exc:
                raise ResultDeliveryError('OutputQueueFailed') from exc
        else:
            logger.debug(f"客户端已断连，丢弃待发送结果: {task.task_id[:8]}")
        if result.is_final:
            self.state.sessions.pop(task.key, None)
        elif task.key in self.state.sessions:
            self.session_activity[task.key] = time.monotonic()

    def loop(self):
        """核心任务循环：drain 队列 → 清理断连 → 轮转执行一个。"""
        logger.info("TaskHandler 开始工作循环 (公平调度)")

        try:
            while True:
                try:
                    if not self.drain_queue():
                        break

                    self.cleanup()
                    task = self.buffer.pop()
                    if task is None:
                        continue

                    # 根据任务类型分派
                    if task.type == 'cmd':
                        self.handle_command_task(task)
                    else:
                        self.handle_audio_task(task)

                    self.cleanup()
                except Exception as exc:
                    # Signal the parent before potentially blocking backend cleanup.
                    if self.failure_event is not None:
                        self.failure_event.set()
                    logger.error('Worker task loop stopped: error=%s', type(exc).__name__)
                    raise
        finally:
            self.state.sessions.clear()
            self.session_activity.clear()
            self.buffer.cleanup_tasks()
            self.state.failed_tasks.retain([])
            self.gpu_monitor.close()

        logger.info("TaskHandler 工作循环结束")
