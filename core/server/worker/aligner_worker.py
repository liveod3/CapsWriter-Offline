# coding: utf-8
"""独立 Forced Aligner 兄弟进程入口。"""

import os
import queue
import time

from config_server import ServerConfig as Config
from core.server.schema import (
    AlignRequest,
    AlignResponse,
    AlignmentItem,
    AlignmentResult,
)
from . import logger


def _safe_timeout() -> float:
    try:
        return max(0.0, float(getattr(Config, 'aligner_idle_timeout', 600)))
    except (TypeError, ValueError):
        return 600.0


def _neutral_result(result) -> AlignmentResult:
    """将 Aligner 私有返回类型转换成不会触发实现模块导入的结构。"""
    items = []
    for item in getattr(result, 'items', []) or []:
        items.append(AlignmentItem(
            text=str(item.text),
            start_time=float(item.start_time),
            end_time=float(item.end_time),
        ))
    return AlignmentResult(items=items)


def _put_response(queue_out, response: AlignResponse) -> None:
    try:
        queue_out.put(response, timeout=1.0)
    except queue.Full:
        logger.error(f"Aligner 响应队列已满，丢弃请求 {response.request_id[:8]}")


def start_aligner_worker(queue_in, queue_out):
    """
    Aligner 进程生命周期入口。

    进程启动时不加载模型；首个请求才加载。模型闲置后退出整个进程，
    由主进程监控器补位一个未加载模型的新进程。
    """
    engine = None
    last_active = time.monotonic()
    idle_timeout = _safe_timeout()
    logger.info(f"Aligner 兄弟进程已拉起 (PID: {os.getpid()})，等待按需加载")

    try:
        while True:
            try:
                request = queue_in.get(timeout=0.5)
            except queue.Empty:
                if (engine is not None and idle_timeout > 0
                        and time.monotonic() - last_active >= idle_timeout):
                    logger.info(
                        f"Aligner 已闲置 {idle_timeout:.0f}s，退出独立进程以释放显存"
                    )
                    return
                continue

            if request is None:
                return
            if not isinstance(request, AlignRequest):
                logger.warning(f"忽略未知 Aligner 请求: {type(request).__name__}")
                continue

            if engine is None:
                logger.info(
                    f"收到任务 {request.task_id[:8]}，正在按需加载 Forced Aligner..."
                )
                from ..engines.factory import EngineFactory
                engine = EngineFactory.create_align_engine()

            try:
                result = engine.align(
                    audio=request.audio,
                    text=request.text,
                    language=request.language,
                    offset_sec=request.offset_sec,
                )
                response = AlignResponse(
                    request_id=request.request_id,
                    task_id=request.task_id,
                    result=_neutral_result(result) if result is not None else None,
                )
            except Exception as exc:
                logger.error(
                    'Alignment request failed: task=%s error=%s',
                    request.task_id[:8], type(exc).__name__,
                )
                response = AlignResponse(
                    request_id=request.request_id,
                    task_id=request.task_id,
                    error=type(exc).__name__,
                )

            last_active = time.monotonic()
            _put_response(queue_out, response)
    except Exception as exc:
        logger.error('Aligner worker stopped: error=%s', type(exc).__name__)
        # multiprocessing prints uncaught exception chains to stderr by default.
        raise SystemExit(1) from None
    finally:
        if engine is not None:
            try:
                engine.cleanup()
            except Exception as exc:
                logger.warning('Aligner cleanup failed: error=%s', type(exc).__name__)
