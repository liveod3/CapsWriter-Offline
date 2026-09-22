# coding: utf-8
"""
WebSocket 接收处理模块

处理客户端发送的音频数据，进行分段和缓冲，提交到识别队列。
"""

from core.i18n import Notice, tr

import asyncio
import json
import queue
import time

import websockets

from ..state import console
from ..schema import Task
from ..delivery import ResultDeliveryError, positive_timeout
from config_server import ServerConfig as Config
from core.protocol import AudioMessage, CancelMessage, RecognitionMessage, ProtocolValidationError
from core.constants import AudioFormat
from core.tools.my_status import Status
from .. import logger


# 麦克风接收状态指示器
status_mic = Status(message_id='server.receiving_mic', spinner='point')


class ClientLimitError(Exception):
    """客户端输入超过策略或资源边界。"""

    def __init__(self, reason: str, close_code: int = 1008):
        super().__init__(reason)
        self.reason = reason
        self.close_code = close_code


class ServerBusyError(ClientLimitError):
    """推理队列已满，请客户端稍后重试。"""

    def __init__(self):
        super().__init__(Notice('validation.ws_recv.server_busy_please_retry_later'), close_code=1013)


def _positive_limit(name: str, default, cast=int):
    """读取正数配置；旧配置缺少字段时使用安全默认值。"""
    try:
        value = cast(getattr(Config, name, default))
    except (TypeError, ValueError):
        value = cast(default)
    return value if value > 0 else cast(default)


class AudioCache:
    """
    音频缓冲区

    用于缓存接收到的音频数据，直到达到分段阈值后提交处理。
    """
    def __init__(self, msg: AudioMessage):
        self.chunks = bytearray()   # 音频数据缓冲，避免 bytes += 重复复制
        self.offset: float = 0.0    # 当前偏移时间（秒）
        self.byte_count: int = 0    # 累计接收字节数
        self.created_at: float = time.monotonic()
        self.last_activity = self.created_at
        self.source = msg.source
        self.seg_duration = msg.seg_duration
        self.seg_overlap = msg.seg_overlap
        self.time_start = msg.time_start
        self.context = msg.context
        self.language = msg.language
        self.supports_task_errors = msg.supports_task_errors
        self.formatting = (Config.format_num, Config.format_spell)

    @property
    def duration(self) -> float:
        """缓冲区音频时长（秒）"""
        return AudioFormat.bytes_to_seconds(len(self.chunks))

    @property
    def total_duration(self) -> float:
        """累计接收的音频总时长（秒）"""
        return AudioFormat.bytes_to_seconds(self.byte_count)

    def reset(self) -> None:
        """重置缓冲区"""
        self.chunks.clear()
        self.offset = 0.0
        self.byte_count = 0

    def validate_metadata(self, msg: AudioMessage) -> None:
        """同一 task 的分段与识别参数在生命周期内不得漂移。"""
        current = (
            msg.source,
            msg.seg_duration,
            msg.seg_overlap,
            msg.context,
            msg.language,
            msg.supports_task_errors,
        )
        expected = (
            self.source,
            self.seg_duration,
            self.seg_overlap,
            self.context,
            self.language,
            self.supports_task_errors,
        )
        if current != expected:
            raise ProtocolValidationError(Notice('validation.ws_recv.metadata_for_the_same_task_id_must_not'))

    def append(self, data: bytes, max_task_audio_bytes: int) -> None:
        """在累计上限内追加音频。"""
        if self.byte_count + len(data) > max_task_audio_bytes:
            raise ClientLimitError(Notice('validation.ws_recv.total_task_audio_exceeds_server_limit'), close_code=1009)
        self.chunks.extend(data)
        self.byte_count += len(data)
        self.last_activity = time.monotonic()


def _put_task(queue_in, task: Task) -> None:
    """事件循环中只做非阻塞入队，满载时向客户端施加背压。"""
    try:
        queue_in.put_nowait(task)
    except queue.Full as exc:
        raise ServerBusyError() from exc
    except (OSError, EOFError, ValueError) as exc:
        raise ResultDeliveryError('InputQueueFailed') from exc


async def message_handler(websocket, msg: AudioMessage, cache: AudioCache, app) -> None:
    """
    处理客户端发送的音频消息

    根据消息中的分段参数，将音频数据分段后提交到识别队列。
    """
    queue_in = app.state.queue_in

    global status_mic
    is_start = cache.byte_count == 0
    socket_id = str(websocket.id)

    cache.validate_metadata(msg)
    max_task_duration = _positive_limit('max_task_duration', 6 * 60 * 60, float)
    if time.monotonic() - cache.created_at > max_task_duration:
        raise ClientLimitError(Notice('validation.ws_recv.task_exceeded_maximum_allowed_duration'))

    # 麦克风首次消息 → GPU 加速
    if is_start and msg.source == 'mic' and Config.gpu_boost_enabled:
        try:
            _put_task(queue_in, Task(
                type='cmd',
                task_id='gpu_boost',
                data=b'', offset=0, overlap=0,
                socket_id=socket_id, is_final=False,
                time_start=0, time_submit=0,
                command='gpu_boost'
            ))
        except ServerBusyError:
            # 加速只是可选优化，不能挤占音频任务容量。
            logger.debug(Notice('diagnostic.ws_recv.inference_queue_full_gpu_boost_command_skipped'))

    # 从消息中获取分段参数
    seg_threshold = msg.seg_duration + msg.seg_overlap * 2

    try:
        data = msg.decode_audio()
        cache.append(
            data,
            _positive_limit('max_task_audio_bytes', 4 * 60 * 60 * 16000 * 4),
        )

        if not msg.is_final:
            # 打印状态消息
            if msg.source == 'mic':
                status_mic.start()
            if msg.source == 'file' and is_start:
                console.print(tr('server.receiving'))
                logger.info(Notice('diagnostic.ws_recv.receiving_audio_file_task', value0=msg.task_id))

            # 若缓冲已达到分段阈值，将片段作为任务提交
            segment_bytes = AudioFormat.seconds_to_bytes(msg.seg_duration + msg.seg_overlap)
            stride_bytes = AudioFormat.seconds_to_bytes(msg.seg_duration)

            while cache.duration >= seg_threshold:
                segment_data = bytes(cache.chunks[:segment_bytes])
                del cache.chunks[:stride_bytes]

                task = Task(
                    type=msg.source,
                    data=segment_data,
                    offset=cache.offset,
                    task_id=msg.task_id,
                    socket_id=socket_id,
                    overlap=msg.seg_overlap,
                    is_final=False,
                    time_start=cache.time_start,
                    time_submit=time.time(),
                    context=msg.context,
                    language=msg.language,
                    supports_task_errors=msg.supports_task_errors,
                    formatting=cache.formatting,
                )
                cache.offset += msg.seg_duration
                _put_task(queue_in, task)
                logger.debug(
                    Notice('diagnostic.ws_recv.submitting_audio_segment_task_offset_s_buffer_bytes', value0=msg.task_id, value1=cache.offset, value2=len(cache.chunks))
                )

        else:  # is_final
            # 打印状态消息
            if msg.source == 'mic':
                status_mic.stop()
            elif msg.source == 'file':
                print(tr('terminal.ws_recv.audio_file_received_duration_s', value0=cache.total_duration))
                logger.info(Notice('diagnostic.ws_recv.audio_file_received_task_duration_s', value0=msg.task_id, value1=cache.total_duration))

            # 提交最终片段
            task = Task(
                type=msg.source,
                data=bytes(cache.chunks),
                offset=cache.offset,
                task_id=msg.task_id,
                socket_id=socket_id,
                overlap=msg.seg_overlap,
                is_final=True,
                time_start=cache.time_start,
                time_submit=time.time(),
                context=msg.context,
                language=msg.language,
                supports_task_errors=msg.supports_task_errors,
                formatting=cache.formatting,
            )
            _put_task(queue_in, task)
            logger.debug(Notice('diagnostic.ws_recv.submitting_final_segment_task_bytes', value0=msg.task_id, value1=len(cache.chunks)))

    except (ClientLimitError, ServerBusyError, ProtocolValidationError, ResultDeliveryError):
        raise
    except Exception as e:
        logger.error(Notice('diagnostic.ws_recv.audio_message_processing_failed_task_error'),
                     msg.task_id[:8], type(e).__name__)
        raise


async def ws_recv(websocket, app) -> None:
    """
    WebSocket 接收主函数

    处理单个客户端连接，接收音频数据并分发处理。
    """
    global status_mic

    # 登记 socket 到连接池
    state = app.state
    sockets = state.sockets
    sockets_id = state.sockets_id
    socket_last_activity = state.socket_last_activity
    socket_id = str(websocket.id)
    sockets[socket_id] = websocket
    sockets_id.append(socket_id)
    socket_last_activity[socket_id] = time.monotonic()
    remote = websocket.remote_address
    console.print(tr('server.connected', value0=remote[0], value1=remote[1]))
    logger.info(Notice('diagnostic.ws_recv.new_client_connected_id', value0=websocket, value1=socket_id))

    # 每个 task_id 独立缓存；数量也受限，避免交错任务混音和无限建 task。
    caches = {}
    state.audio_caches[socket_id] = caches
    max_tasks = _positive_limit('max_tasks_per_connection', 4)
    idle_timeout = _positive_limit('connection_idle_timeout', 300, float)
    max_audio_bytes = _positive_limit('max_message_audio_bytes', 4 * 1024 * 1024)
    max_context_length = _positive_limit('max_context_length', 4096)
    task_timeout = positive_timeout(Config, 'worker_stall_timeout', 600.0)

    # 接收并处理消息
    try:
        while True:
            if any(time.monotonic() - cache.last_activity >= task_timeout
                   for cache in caches.values()):
                raise ClientLimitError(Notice('server.input_timeout'))
            try:
                raw_message = await asyncio.wait_for(websocket.recv(), timeout=min(idle_timeout, task_timeout))
            except asyncio.TimeoutError:
                idle_for = time.monotonic() - socket_last_activity.get(socket_id, 0)
                if idle_for >= idle_timeout:
                    raise ClientLimitError(Notice('validation.ws_recv.connection_idle_timeout'))
                continue

            socket_last_activity[socket_id] = time.monotonic()

            # The sender may revoke this connection during a failed close.
            if sockets.get(socket_id) is not websocket:
                break

            try:
                if not isinstance(raw_message, str):
                    raise ProtocolValidationError(Notice('validation.ws_recv.only_json_text_messages_are_accepted'))
                data = json.loads(raw_message)
                if isinstance(data, dict) and data.get('type') == 'cancel':
                    cancellation = CancelMessage.from_dict(data)
                    if state.failed_tasks.contains(socket_id, cancellation.task_id):
                        continue
                    close_connection = state.failed_tasks.add(socket_id, cancellation.task_id)
                    caches.pop(cancellation.task_id, None)
                    _put_task(state.queue_in, Task(
                        'cmd', b'', 0, 0, cancellation.task_id, socket_id,
                        True, 0, time.time(), command='cancel',
                    ))
                    response = RecognitionMessage(
                        cancellation.task_id, True, 0, 0, 0, time.time(), '',
                        error_code='cancelled',
                    )
                    await asyncio.wait_for(websocket.send(response.to_json()), 5.0)
                    if not any(cache.source == 'mic' for items in state.audio_caches.values()
                               for cache in items.values()):
                        status_mic.stop()
                    if close_connection:
                        raise ClientLimitError(Notice('server.cancellation_limit'))
                    continue
                msg = AudioMessage.from_dict(
                    data,
                    max_audio_bytes=max_audio_bytes,
                    max_context_length=max_context_length,
                )
                if state.failed_tasks.contains(socket_id, msg.task_id):
                    continue
                cache = caches.get(msg.task_id)
                if cache is None:
                    if len(caches) >= max_tasks:
                        raise ClientLimitError(Notice('validation.ws_recv.concurrent_tasks_per_connection_exceed_server_limit'))
                    cache = AudioCache(msg)
                    caches[msg.task_id] = cache
                await message_handler(websocket, msg, cache, app)
                if msg.is_final:
                    caches.pop(msg.task_id, None)
            except (json.JSONDecodeError, ProtocolValidationError) as exc:
                raise ClientLimitError(Notice('validation.ws_recv.invalid_message_format', value0=exc)) from exc

    except ResultDeliveryError as exc:
        if state.worker_failed is not None:
            state.worker_failed.set()
        logger.error(Notice('diagnostic.ws_recv.input_channel_unavailable'), str(exc))

    except ClientLimitError as exc:
        logger.warning(Notice('diagnostic.ws_recv.client_message_rejected_id', value0=socket_id, value1=exc.reason))
        await websocket.close(code=exc.close_code, reason=exc.reason)

    except websockets.ConnectionClosed:
        console.print(tr('server.connection_closed'))
        logger.warning(Notice('diagnostic.ws_recv.client_connection_closed', value0=socket_id))
    except websockets.InvalidState:
        console.print(tr('server.invalid_connection'))
        logger.error(Notice('diagnostic.ws_recv.invalid_websocket_state', value0=socket_id))
    except Exception as e:
        logger.error(Notice('diagnostic.ws_recv.websocket_receive_failed_socket_error'),
                     socket_id[:8], type(e).__name__)
    finally:
        # 清理资源
        status_mic.stop()
        status_mic.on = False
        sockets.pop(socket_id, None)
        socket_last_activity.pop(socket_id, None)
        caches.clear()
        state.audio_caches.pop(socket_id, None)
        state.failed_tasks.discard_connection(socket_id)
        if socket_id in sockets_id:
            sockets_id.remove(socket_id)

        console.print(tr('server.disconnected', value0=remote[0], value1=remote[1]))

        # 注意：session 清理由 TaskHandler 在子进程中定期执行
        # （通过检查 sockets_id 判断客户端是否已断开）
        logger.debug(Notice('diagnostic.ws_recv.client_resources_cleaned_up', value0=socket_id))
