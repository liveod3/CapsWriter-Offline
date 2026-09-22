"""Bound result delivery and stop permanently broken shared channels."""

from core.i18n import Notice, tr

import asyncio
import queue
import time

from config_server import ServerConfig as Config
from core.protocol import RecognitionMessage
from core.tools.asyncio_to_thread import to_thread
from ..state import console
from ..schema import Result
from ..delivery import ResultDeliveryError, positive_timeout
from .. import logger


TASK_ERROR_IO_TIMEOUT = 5.0
RESULT_QUEUE_POLL = 0.25


async def _close_failed_connection(websocket, reason):
    """Abort on a stalled close or cancellation; never abandon its transport."""
    try:
        await asyncio.wait_for(
            websocket.close(code=1011, reason=reason), TASK_ERROR_IO_TIMEOUT)
    except asyncio.CancelledError:
        transport = getattr(websocket, 'transport', None)
        if transport is not None:
            transport.abort()
        raise
    except Exception as exc:
        logger.warning(Notice('diagnostic.ws_send.result_connection_cleanup_failed'), type(exc).__name__)
        transport = getattr(websocket, 'transport', None)
        if transport is not None:
            transport.abort()


async def _retire_connection(state, websocket, reason):
    """Revoke input/worker ownership before awaiting the close handshake."""
    socket_id = str(websocket.id)
    state.sockets.pop(socket_id, None)
    state.socket_last_activity.pop(socket_id, None)
    state.audio_caches.pop(socket_id, {}).clear()
    state.failed_tasks.discard_connection(socket_id)
    try:
        if state.sockets_id is not None and socket_id in state.sockets_id:
            state.sockets_id.remove(socket_id)
    except (OSError, EOFError, ValueError) as exc:
        raise ResultDeliveryError('ConnectionRegistryFailed') from exc
    finally:
        await _close_failed_connection(websocket, reason)


def _check_worker(state):
    try:
        failure = getattr(state, 'worker_failed', None)
        if failure is not None and failure.is_set():
            raise ResultDeliveryError('WorkerChannelFailed')
        process = getattr(state, 'recognize_process', None)
        if process is not None and not process.is_alive():
            raise ResultDeliveryError('WorkerExited')
    except (OSError, EOFError, ValueError) as exc:
        raise ResultDeliveryError('WorkerStatusUnavailable') from exc


async def _next_result(app, timeout):
    """Own one read even if a damaged IPC pipe ignores its queue timeout."""
    state = app.state
    operation = asyncio.create_task(to_thread(state.queue_out.get, timeout=RESULT_QUEUE_POLL))
    deadline = time.monotonic() + timeout
    try:
        while True:
            done, _ = await asyncio.wait({operation}, timeout=RESULT_QUEUE_POLL)
            if getattr(app, 'is_alive', True) is False:
                return None
            _check_worker(state)
            if done:
                return operation.result()
            if time.monotonic() >= deadline:
                raise ResultDeliveryError('ResultQueueReadTimeout')
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


async def ws_send(app):
    state = app.state
    send_timeout = positive_timeout(Config, 'result_send_timeout', 10.0)
    queue_timeout = positive_timeout(Config, 'result_queue_timeout', 60.0)
    logger.info(Notice('diagnostic.ws_send.result_sender_started'))
    while True:
        if getattr(app, 'is_alive', True) is False:
            return
        _check_worker(state)
        try:
            # Only one executor read is outstanding; never retry a stalled pipe.
            result = await _next_result(app, queue_timeout)
        except queue.Empty:
            continue
        except ResultDeliveryError:
            raise
        except Exception as exc:
            raise ResultDeliveryError('ResultQueueFailed') from exc
        if result is None:
            return
        if not isinstance(result, Result):
            raise ResultDeliveryError('InvalidResultQueueItem')
        failures = getattr(state, 'failed_tasks', None)
        if failures is not None and failures.contains(result.socket_id, result.task_id):
            continue
        websocket = state.sockets.get(result.socket_id)
        if websocket is None:
            continue

        close_connection = False
        if result.error_code:
            close_connection = state.failed_tasks.add(result.socket_id, result.task_id)
            state.audio_caches.get(result.socket_id, {}).pop(result.task_id, None)
            if result.type == 'mic':
                from .ws_recv import status_mic
                if not any(cache.source == 'mic' for caches in state.audio_caches.values()
                           for cache in caches.values()):
                    status_mic.stop()
            if not result.supports_task_errors:
                await _retire_connection(state, websocket, 'Recognition task failed')
                continue

        try:
            message = RecognitionMessage(
                task_id=result.task_id, is_final=result.is_final, duration=result.duration,
                time_start=result.time_start, time_submit=result.time_submit,
                time_complete=result.time_complete, text=result.text,
                text_accu=result.text_accu, tokens=result.tokens,
                timestamps=result.timestamps, error_code=result.error_code,
            )
            timeout = TASK_ERROR_IO_TIMEOUT if result.error_code else send_timeout
            await asyncio.wait_for(websocket.send(message.to_json()), timeout)
        except asyncio.CancelledError:
            # A partially canceled send cannot be reused safely.
            transport = getattr(websocket, 'transport', None)
            if transport is not None:
                transport.abort()
            raise
        except Exception as exc:
            logger.warning(Notice('diagnostic.ws_send.result_delivery_failed_socket_task_error'),
                           result.socket_id[:8], result.task_id[:8], type(exc).__name__)
            reason = 'Recognition task failed' if result.error_code else 'Result delivery failed'
            await _retire_connection(state, websocket, reason)
            continue

        if state.sockets.get(result.socket_id) is websocket:
            state.socket_last_activity[result.socket_id] = time.monotonic()
        logger.debug(Notice('diagnostic.ws_send.result_delivered_task_chars_final'),
                     result.task_id[:8], len(result.text), result.is_final)
        if result.error_code:
            logger.warning(Notice('diagnostic.ws_send.task_failure_delivered_socket_task_code'),
                           result.socket_id[:8], result.task_id[:8], result.error_code)
            if close_connection or result.close_connection:
                await _retire_connection(state, websocket, 'Task failure limit reached; reconnect')
        elif result.type == 'file':
            console.print(tr('server.progress', value0=result.duration), end='\r')
            if result.is_final:
                console.print(tr('server.complete'))
