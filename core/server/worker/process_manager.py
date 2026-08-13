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
from multiprocessing import Process, Manager
from typing import TYPE_CHECKING
from ..state import console
from . import start_worker
from .aligner_worker import start_aligner_worker
from .check_model import check_model
from . import logger
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
        check_model()

        # 2. 初始化共享资源
        # 使用 Manager 管理共享列表，用于追踪活动连接
        state = self.app.state
        state.sockets_id = Manager().list()
        
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
                  stdin_fn),
            daemon=True
        )
        self._process.start()
        
        # 存入状态以便其他模块引用
        state.recognize_process = self._process
        logger.info(f"识别子进程已拉起 (PID: {self._process.pid})")

        # 5. 等待模型加载完成 (轮询方式)
        self._wait_for_models()

        # Aligner 空闲退出或异常退出后，由主进程自动补位一个空载进程。
        if self.is_alive:
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
            state.aligner_process = self._align_process
            logger.info(f"Aligner 兄弟进程已拉起 (PID: {self._align_process.pid})")
            return self._align_process

    def _monitor_aligner(self):
        """监控 Aligner 的空闲退出/异常退出并自动补位。"""
        while not self._monitor_stop.wait(0.5):
            if not self.is_alive:
                return
            process = self._align_process
            if process is not None and not process.is_alive():
                if process.exitcode not in (0, None):
                    logger.error(
                        f"Aligner 进程异常退出 (PID: {process.pid}, "
                        f"ExitCode: {process.exitcode})，正在自动重启"
                    )
                else:
                    logger.info("Aligner 空闲进程已退出，正在补位空载进程")
                self._start_aligner_process()

    def _wait_for_models(self):
        """轮询队列直到收到模型加载成功 (True) 或发生错误"""
        logger.info("正在等待子进程加载模型...")
        
        while self.is_alive:
            try:
                # 阻塞最多 100ms
                status = self.app.state.queue_out.get(timeout=0.1)
                if status is True:
                    # 收到 True 说明模型加载成功
                    break
            except (queue.Empty, OSError):
                if self._process and not self._process.is_alive():
                    self._handle_unexpected_exit()
                    return
                continue
            
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

        align_process = self._align_process
        if align_process and align_process.is_alive():
            logger.info(f"正在停止 Aligner 兄弟进程 (PID: {align_process.pid})...")
            try:
                self.app.state.align_queue_in.put(None, timeout=0.5)
            except queue.Full:
                logger.debug('Aligner 输入队列已满，将通过进程终止兜底退出')

            align_process.join(timeout=2)
            if align_process.is_alive():
                logger.debug("Aligner 进程未响应优雅退出，执行强制终止")
                align_process.terminate()
                align_process.join(timeout=1)

        if self._process and self._process.is_alive():
            logger.info(f"正在终止识别子进程 (PID: {self._process.pid})...")
            # 发送 None 任务通知优雅退出 (作为兜底)

            try:
                self.app.state.queue_in.put(None, timeout=0.5)
            except queue.Full:
                logger.debug('输入队列已满，将通过进程终止兜底退出')
            
            # 如果 2 秒内没退，则强制 kill
            self._process.join(timeout=2)
            if self._process.is_alive():
                logger.debug("子进程未响应优雅退出，执行强制终止")
                self._process.terminate()
