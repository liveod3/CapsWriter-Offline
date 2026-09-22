# coding: utf-8
"""
识别子进程管理器 (ProcessManager)

负责维护单机识别进程的生命周期，包括启动、模型加载监控、异常退出捕获。
"""
from __future__ import annotations
import sys
import os
import queue
import threading
import time
from collections import deque
from multiprocessing import Process, Manager, Event, Value
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING
from rich.panel import Panel
from config_server import ServerConfig as Config
from ..state import console
from . import start_worker
from .aligner_worker import start_aligner_worker
from .check_model import check_model
from . import logger
from ..delivery import ResultDeliveryError, positive_timeout
from .supervision import progress
from core.tools.daemon_executor import SimpleDaemonExecutor
if TYPE_CHECKING:
    from ..app import CapsWriterServer


class ProcessManager:
    """
    识别子进程管理器
    
    由 CapsWriterServer 调用，专注于进程层级的控制。
    """
    def __init__(self, app: CapsWriterServer):
        self._process = None
        self._align_process = None
        self._align_lock = threading.Lock()
        self._align_monitor_thread = None
        self._monitor_stop = threading.Event()
        self._aligner_idle_exits = deque()
        self._last_aligner_churn_warning = 0.0
        self._manager = None
        self.app = app
        self.is_alive = False

    def start(self):
        """
        启动识别子进程并等待模型加载完成
        
        Returns:
            Process: 启动成功的子进程对象
        """
        # 防连续触发
        if self.is_alive: return
        self.is_alive = True
        self._monitor_stop.clear()

        # 1. 前置检查
        check_model(interactive=False)

        # 2. 初始化共享资源
        # 使用 Manager 管理共享列表，用于追踪活动连接
        state = self.app.state
        self._manager = Manager()
        state.sockets_id = self._manager.list()
        state.worker_failed = Event()
        state.worker_progress = Value('d', time.monotonic())
        
        # 获取标准输入文件描述符，用于 Windows 下的信号传递补丁
        stdin_fn = sys.stdin.fileno()
        
        # 3. 先启动轻量 Aligner 兄弟进程（首个文件请求前不会加载模型）
        self._start_aligner_process()

        # 4. 创建并启动 ASR 进程
        self._process = Process(
            target=start_worker,
            args=(state.queue_in,
                  state.queue_out,
                  state.sockets_id,
                  state.align_queue_in,
                  state.align_queue_out,
                  stdin_fn,
                  state.worker_failed,
                  state.worker_progress),
            daemon=True
        )
        self._process.start()
        
        # 存入状态以便其他模块引用
        state.recognize_process = self._process
        logger.info(f"识别子进程已拉起 (PID: {self._process.pid})")

        # 5. 等待模型加载完成 (轮询方式)
        self._wait_for_models()

        # Monitor ASR progress and replace only clean idle aligner exits.
        requested = getattr(self.app, '_stop_requested', None)
        if self.is_alive and not (requested is not None and requested.is_set()):
            self._align_monitor_thread = threading.Thread(
                target=self._monitor_aligner,
                name='aligner-process-monitor',
                daemon=True,
            )
            self._align_monitor_thread.start()
        
        return self._process

    def _start_aligner_process(self):
        """确保存在一个只等待请求、尚未必加载模型的 Aligner 进程。"""
        with self._align_lock:
            if not self.is_alive:
                return None
            if self._align_process and self._align_process.is_alive():
                return self._align_process

            old_process = self._align_process
            if old_process is not None:
                try:
                    old_process.join(timeout=0)
                    old_process.close()
                except (OSError, ValueError):
                    pass

            state = self.app.state
            self._align_process = Process(
                target=start_aligner_worker,
                args=(state.align_queue_in, state.align_queue_out),
                daemon=True,
            )
            self._align_process.start()
            if not self.is_alive:
                self._align_process.terminate()
                self._align_process.join(timeout=1)
                return None
            state.aligner_process = self._align_process
            logger.info(f"Aligner 兄弟进程已拉起 (PID: {self._align_process.pid})")
            return self._align_process

    def _monitor_aligner(self):
        """Replace clean idle exits only; stop the service on death or lost progress."""
        while not self._monitor_stop.wait(0.5):
            if not self.is_alive:
                return
            try:
                self._check_runtime()
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ResultDeliveryError) else type(exc).__name__
                logger.error('Worker supervision stopped service: %s', reason)
                self.app.state.worker_failed.set()
                return

    def _check_runtime(self):
        state = self.app.state
        if state.worker_failed.is_set():
            raise ResultDeliveryError('WorkerChannelFailed')
        if self._process is not None and not self._process.is_alive():
            raise ResultDeliveryError('WorkerExited')
        timeout = positive_timeout(Config, 'worker_stall_timeout', 600.0)
        if time.monotonic() - progress(state.worker_progress) >= timeout:
            raise ResultDeliveryError('WorkerStalled')
        with self._align_lock:
            process = self._align_process
            if process is not None and not process.is_alive():
                if process.exitcode not in (0, None):
                    raise ResultDeliveryError('AlignerExited')
                else:
                    logger.info('Replacing an idle aligner process')
                    self._record_aligner_idle_exit()
            else:
                return
        self._start_aligner_process()

    def _record_aligner_idle_exit(self):
        """识别短时间内反复卸载/重载 Aligner 的资源抖动。"""
        now = time.monotonic()
        window = 60.0
        self._aligner_idle_exits.append(now)
        while self._aligner_idle_exits and now - self._aligner_idle_exits[0] > window:
            self._aligner_idle_exits.popleft()

        if len(self._aligner_idle_exits) < 3:
            return
        if (self._last_aligner_churn_warning
                and now - self._last_aligner_churn_warning < 300):
            return

        self._last_aligner_churn_warning = now
        timeout = getattr(Config, 'aligner_idle_timeout', 600)
        logger.warning(
            f'检测到 Aligner 在 60 秒内反复退出 {len(self._aligner_idle_exits)} 次；'
            f'aligner_idle_timeout={timeout!r} 可能过短'
        )
        console.print(Panel.fit(
            f'[bold yellow]60 秒内已发生 {len(self._aligner_idle_exits)} 次 '
            'Aligner 卸载/重载。[/bold yellow]\n'
            '这会造成专用显存和 GPU 利用率呈锯齿波动，并拖慢文件转写；'
            '[bold]它不等同于显存交换[/bold]。\n'
            f'[dim]当前 aligner_idle_timeout = {timeout!r}。若显存容得下 ASR 与 '
            'Aligner 同时驻留，可尝试提高到 30；设为 0 表示常驻。[/dim]',
            title='[bold yellow]GPU 模型反复装卸告警[/bold yellow]',
            border_style='bold yellow',
        ))

    def _wait_for_models(self):
        """Own one daemon read and bound startup even when a pipe read is wedged."""
        logger.info('Waiting for recognition models')
        deadline = time.monotonic() + positive_timeout(Config, 'model_startup_timeout', 300.0)
        read = None
        executor = SimpleDaemonExecutor()
        while self.is_alive:
            requested = getattr(self.app, '_stop_requested', None)
            if requested is not None and requested.is_set():
                self.app.stop()
                return
            failure = getattr(self.app.state, 'worker_failed', None)
            if failure is not None and failure.is_set():
                logger.error('Recognition worker failed during startup; stopping server')
                self.app.stop()
                return
            if time.monotonic() >= deadline:
                logger.error('Model startup timed out; restart required')
                self.app.stop()
                return
            if self._process and not self._process.is_alive():
                self._handle_unexpected_exit()
                return
            try:
                if read is None:
                    read = executor.submit(self.app.state.queue_out.get, timeout=0.1)
                status = read.result(timeout=0.1)
                read = None
                if status is True:
                    progress(getattr(self.app.state, 'worker_progress', None), update=True)
                    break
                raise ResultDeliveryError('InvalidStartupResult')
            except FutureTimeout:
                if read.done():
                    logger.error('Model startup reader failed; restart required')
                    self.app.stop()
                    return
                continue
            except queue.Empty:
                read = None
            except Exception as exc:
                logger.error('Model startup channel failed: %s', type(exc).__name__)
                self.app.stop()
                return
            
        if not self.is_alive: return
        logger.info("模型加载完成，ASR 服务就绪")
        console.rule('[green3]开始服务')
        console.line()

    def _handle_unexpected_exit(self):
        """处理子进程加载模型时的意外退出"""
        exit_code = self._process.exitcode
        if exit_code != 0:
            logger.error(f"识别子进程意外退出! ExitCode: {exit_code}")
            logger.error("这通常是由于模型损坏、底层库冲突或系统资源不足导致的。")
        
        # 请求主系统同步退出
        self.app.stop()

    def stop(self):
        """停止子进程"""

        # 防连续触发
        if not self.is_alive: return
        self.is_alive = False
        self._monitor_stop.set()

        if self._align_monitor_thread and self._align_monitor_thread.is_alive():
            self._align_monitor_thread.join(timeout=1)

        for process, channel in (
                (self._align_process, getattr(self.app.state, 'align_queue_in', None)),
                (self._process, getattr(self.app.state, 'queue_in', None))):
            if process is None:
                continue
            try:
                self._stop_process(process, channel)
            except Exception as exc:
                logger.error('Process cleanup failed: %s', type(exc).__name__)

        if self._manager is not None:
            try:
                self._manager.shutdown()
            except Exception as exc:
                logger.error('Shared registry cleanup failed: %s', type(exc).__name__)
            finally:
                self._manager = None
        for name in ('queue_in', 'queue_out', 'align_queue_in', 'align_queue_out'):
            channel = getattr(self.app.state, name, None)
            if channel is not None and hasattr(channel, 'cancel_join_thread'):
                # Never join a feeder writing to a child that was terminated.
                try:
                    channel.cancel_join_thread()
                    channel.close()
                except (OSError, ValueError) as exc:
                    logger.debug('Queue cleanup failed: %s', type(exc).__name__)

    @staticmethod
    def _stop_process(process, channel):
        if process.is_alive():
            try:
                channel.put(None, timeout=0.5)
            except (queue.Full, OSError, EOFError, ValueError) as exc:
                logger.debug('Worker shutdown signal unavailable: %s', type(exc).__name__)
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
        else:
            process.join(timeout=0)
        if process.is_alive():
            logger.error('Worker did not exit after termination')
