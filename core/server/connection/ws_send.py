import json
import asyncio
import time
from multiprocessing import Queue

from ..state import console
from ..schema import Result
from core.protocol import RecognitionMessage
from core.tools.asyncio_to_thread import to_thread
from .. import logger


TASK_ERROR_IO_TIMEOUT = 5.0


async def _close_failed_connection(websocket, reason):
    """Bound error fallback cleanup and abort a stalled close handshake."""
    try:
        await asyncio.wait_for(
            websocket.close(code=1011, reason=reason), TASK_ERROR_IO_TIMEOUT)
    except Exception as exc:
        logger.warning('Task failure connection cleanup failed: %s', type(exc).__name__)
        transport = getattr(websocket, 'transport', None)
        if transport is not None:
            transport.abort()

async def ws_send(app):

    state = app.state
    queue_out = state.queue_out
    sockets = state.sockets

    logger.info("WebSocket 发送任务已启动")

    while True:
        try:
            # 获取识别结果（从多进程队列）
            result: Result = await to_thread(queue_out.get)

            # 得到退出的通知
            if result is None:
                logger.info("收到退出通知，停止发送任务")
                return

            failures = getattr(state, 'failed_tasks', None)
            if failures is not None and failures.contains(result.socket_id, result.task_id):
                continue

            # 1. 将内部 Result 转换为标准的协议消息对象
            msg = RecognitionMessage(
                task_id=result.task_id,
                is_final=result.is_final,
                duration=result.duration,
                time_start=result.time_start,
                time_submit=result.time_submit,
                time_complete=result.time_complete,
                text=result.text,
                text_accu=result.text_accu,
                tokens=result.tokens,
                timestamps=result.timestamps,
                error_code=result.error_code,
            )

            # 获得 socket
            websocket = next(
                (ws for ws in sockets.values() if str(ws.id) == result.socket_id),
                None,
            )

            if not websocket:
                logger.warning(f"客户端 {result.socket_id} 不存在，跳过发送结果，任务ID: {result.task_id}")
                continue

            if result.error_code:
                close_connection = state.failed_tasks.add(result.socket_id, result.task_id)
                state.audio_caches.get(result.socket_id, {}).pop(result.task_id, None)
                if result.type == 'mic':
                    from .ws_recv import status_mic
                    if not any(cache.source == 'mic' for caches in state.audio_caches.values()
                               for cache in caches.values()):
                        status_mic.stop()
                # Old clients would interpret an empty final as successful text.
                # Closing their connection preserves an observable failure.
                if not result.supports_task_errors:
                    await _close_failed_connection(websocket, 'Recognition task failed')
                    continue

            # 发送消息
            if result.error_code:
                try:
                    await asyncio.wait_for(
                        websocket.send(msg.to_json()), TASK_ERROR_IO_TIMEOUT)
                except Exception as exc:
                    logger.warning('Task failure delivery failed: socket=%s task=%s error=%s',
                                   result.socket_id[:8], result.task_id[:8], type(exc).__name__)
                    await _close_failed_connection(websocket, 'Recognition task failed')
                    continue
            else:
                await websocket.send(msg.to_json())
            state.socket_last_activity[result.socket_id] = time.monotonic()
            logger.debug(f"发送识别结果，任务ID: {result.task_id}, 文本长度: {len(result.text)}")

            if result.error_code:
                logger.warning('Task failure delivered: socket=%s task=%s code=%s',
                               result.socket_id[:8], result.task_id[:8], result.error_code)
                if close_connection or result.close_connection:
                    await _close_failed_connection(
                        websocket, 'Task failure limit reached; reconnect')
                continue

            if result.type == 'mic':
                logger.info(f"麦克风识别结果: {result.text}")
            elif result.type == 'file':
                console.print(f'    转录进度：{result.duration:.2f}s', end='\r')
                logger.debug(f"文件转录进度: {result.duration:.2f}s")
                if result.is_final:
                    console.print('\n    [green]转录完成')
                    logger.info(f"文件转录完成，任务ID: {result.task_id}, 总时长: {result.duration:.2f}s")

        except Exception as e:
            logger.error(f"发送结果时发生错误: {e}", exc_info=True)
            print(e)
