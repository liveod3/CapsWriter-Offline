# coding: utf-8
"""Process queued audio and return recognition results to the main process.

Schedule (socket_id, task_id) pairs round-robin, preserving FIFO within each pair.
"""

from core.i18n import Notice

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
        """Select the next session task in round-robin order, or None when empty."""
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
                    Notice('diagnostic.task_handler.removed_buffered_session_socket_task', value0=key[0][:8], value1=key[1][:8])
                )
                del self._buffers[key]

    @property
    def is_empty(self) -> bool:
        return len(self._buffers) == 0

    @property
    def task_count(self) -> int:
        """Return the number of buffered segments in this process."""
        return sum(len(buffer) for buffer in self._buffers.values())


class TaskHandler:
    """
    Task handler.

    Coordinate queues and the recognition pipeline.
    Schedule tasks in fair round-robin order.
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
        # Import lazily to avoid server state dependencies during module initialization.
        from ..state import console
        return console

    def set_engine(self, recognizer, punc_model=None, aligner=None):
        """Inject engines and initialize the pipeline."""
        self.recognizer = recognizer
        self.punc_model = punc_model
        self.aligner = aligner
        self.pipeline = TaskPipeline(recognizer, punc_model, aligner, self.state)

    def drain_queue(self) -> bool:
        """Drain queued tasks into the bounded buffer; False signals shutdown."""
        while True:
            progress(self.progress_clock, update=True)
            # Continuously draining a bounded queue can still grow local memory.
            # Process a buffered segment before receiving more at capacity.
            if self.buffer.task_count >= self.max_buffer_tasks:
                return True

            # Read a task.
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
            
            # Check for shutdown.
            if task is None:
                return False

            # Skip tasks from disconnected clients.
            if task.socket_id not in self.sockets_id:
                logger.debug(Notice('diagnostic.task_handler.skipping_disconnected_client_task', value0=task.task_id[:8]))
                continue

            if task.type == 'cmd' and task.command == 'cancel':
                self.state.failed_tasks.add(task.socket_id, task.task_id)
                self.state.sessions.pop(task.key, None)
                self.buffer.cleanup_tasks()
                continue

            # Buffer the task.
            self.buffer.enqueue(task)
            if task.key in self.state.sessions:
                self.session_activity[task.key] = time.monotonic()

    def cleanup(self):
        """Remove buffered tasks and sessions for disconnected sockets."""
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
        """Release idle alignment resources and reset GPU boost."""
        if self.pipeline and self.pipeline.aligner:
            self.pipeline.aligner.check_idle()
        self.gpu_boost.check_idle()

    def handle_command_task(self, task):
        """Process a command task."""
        try:
            self.gpu_boost.handle_command(task)
        finally:
            self.state.sessions.pop(task.key, None)

    def handle_audio_task(self, task):
        """Process an audio recognition task."""
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
            logger.error(Notice('diagnostic.task_handler.recognition_task_failed_socket_task_error'),
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
            logger.debug(Notice('diagnostic.task_handler.client_disconnected_dropping_pending_result', value0=task.task_id[:8]))
        if result.is_final:
            self.state.sessions.pop(task.key, None)
        elif task.key in self.state.sessions:
            self.session_activity[task.key] = time.monotonic()

    def loop(self):
        """Drain input, clean disconnected sessions, then execute one scheduled task."""
        logger.info(Notice('diagnostic.task_handler.taskhandler_loop_started_fair_scheduling'))

        try:
            while True:
                try:
                    if not self.drain_queue():
                        break

                    self.cleanup()
                    task = self.buffer.pop()
                    if task is None:
                        continue

                    # Dispatch by task type.
                    if task.type == 'cmd':
                        self.handle_command_task(task)
                    else:
                        self.handle_audio_task(task)

                    self.cleanup()
                except Exception as exc:
                    # Signal the parent before potentially blocking backend cleanup.
                    if self.failure_event is not None:
                        self.failure_event.set()
                    logger.error(Notice('diagnostic.task_handler.worker_task_loop_stopped_error'), type(exc).__name__)
                    raise
        finally:
            self.state.sessions.clear()
            self.session_activity.clear()
            self.buffer.cleanup_tasks()
            self.state.failed_tasks.retain([])
            self.gpu_monitor.close()

        logger.info(Notice('diagnostic.task_handler.taskhandler_loop_ended'))
