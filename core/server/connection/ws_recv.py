# coding: utf-8
"""
WebSocket input handling.

Buffer and segment incoming audio, then enqueue recognition tasks.
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


# Microphone receive indicator.
status_mic = Status(message_id='server.receiving_mic', spinner='point')


class ClientLimitError(Exception):
    """Client input exceeds policy or resource limits."""

    def __init__(self, reason: str, close_code: int = 1008):
        super().__init__(reason)
        self.reason = reason
        self.close_code = close_code


class ServerBusyError(ClientLimitError):
    """The inference queue has no capacity for another task."""

    def __init__(self):
        super().__init__(Notice('validation.ws_recv.server_busy_please_retry_later'), close_code=1013)


def _positive_limit(name: str, default, cast=int):
    """Read a positive setting with a safe fallback for legacy configurations."""
    try:
        value = cast(getattr(Config, name, default))
    except (TypeError, ValueError):
        value = cast(default)
    return value if value > 0 else cast(default)


class AudioCache:
    """
    Audio buffer.

    Buffer incoming audio until enough samples are available for a segment.
    """
    def __init__(self, msg: AudioMessage):
        self.chunks = bytearray()   # Avoid repeated bytes concatenation and copying.
        self.offset: float = 0.0    # Current offset in seconds.
        self.byte_count: int = 0    # Total received bytes.
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
        """Return buffered duration in seconds."""
        return AudioFormat.bytes_to_seconds(len(self.chunks))

    @property
    def total_duration(self) -> float:
        """Return total received audio duration in seconds."""
        return AudioFormat.bytes_to_seconds(self.byte_count)

    def reset(self) -> None:
        """Reset the buffer."""
        self.chunks.clear()
        self.offset = 0.0
        self.byte_count = 0

    def validate_metadata(self, msg: AudioMessage) -> None:
        """Keep segmentation and recognition parameters fixed for the task's lifetime."""
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
        """Append audio within cumulative limits."""
        if self.byte_count + len(data) > max_task_audio_bytes:
            raise ClientLimitError(Notice('validation.ws_recv.total_task_audio_exceeds_server_limit'), close_code=1009)
        self.chunks.extend(data)
        self.byte_count += len(data)
        self.last_activity = time.monotonic()


def _put_task(queue_in, task: Task) -> None:
    """Enqueue without blocking the event loop; apply backpressure at capacity."""
    try:
        queue_in.put_nowait(task)
    except queue.Full as exc:
        raise ServerBusyError() from exc
    except (OSError, EOFError, ValueError) as exc:
        raise ResultDeliveryError('InputQueueFailed') from exc


async def message_handler(websocket, msg: AudioMessage, cache: AudioCache, app) -> None:
    """
    Handle an incoming audio message.

    Split audio using the message's segmentation settings and enqueue recognition work.
    """
    queue_in = app.state.queue_in

    global status_mic
    is_start = cache.byte_count == 0
    socket_id = str(websocket.id)

    cache.validate_metadata(msg)
    max_task_duration = _positive_limit('max_task_duration', 6 * 60 * 60, float)
    if time.monotonic() - cache.created_at > max_task_duration:
        raise ClientLimitError(Notice('validation.ws_recv.task_exceeded_maximum_allowed_duration'))

    # Request optional GPU boost on the first microphone message.
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
            # Boost requests must not consume capacity reserved for audio tasks.
            logger.debug(Notice('diagnostic.ws_recv.inference_queue_full_gpu_boost_command_skipped'))

    # Read segmentation settings from the message.
    seg_threshold = msg.seg_duration + msg.seg_overlap * 2

    try:
        data = msg.decode_audio()
        cache.append(
            data,
            _positive_limit('max_task_audio_bytes', 4 * 60 * 60 * 16000 * 4),
        )

        if not msg.is_final:
            # Display status.
            if msg.source == 'mic':
                status_mic.start()
            if msg.source == 'file' and is_start:
                console.print(tr('server.receiving'))
                logger.info(Notice('diagnostic.ws_recv.receiving_audio_file_task', value0=msg.task_id))

            # Enqueue a segment when the buffer reaches the duration threshold.
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
            # Display status.
            if msg.source == 'mic':
                status_mic.stop()
            elif msg.source == 'file':
                print(tr('terminal.ws_recv.audio_file_received_duration_s', value0=cache.total_duration))
                logger.info(Notice('diagnostic.ws_recv.audio_file_received_task_duration_s', value0=msg.task_id, value1=cache.total_duration))

            # Submit the final segment.
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
    Receive WebSocket messages.

    Read and dispatch audio for one client connection.
    """
    global status_mic

    # Register the socket in the connection pool.
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

    # Bound independent task buffers to avoid mixed audio and unlimited task creation.
    caches = {}
    state.audio_caches[socket_id] = caches
    max_tasks = _positive_limit('max_tasks_per_connection', 4)
    idle_timeout = _positive_limit('connection_idle_timeout', 300, float)
    max_audio_bytes = _positive_limit('max_message_audio_bytes', 4 * 1024 * 1024)
    max_context_length = _positive_limit('max_context_length', 4096)
    task_timeout = positive_timeout(Config, 'worker_stall_timeout', 600.0)

    # Receive and process messages.
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
        # Release resources.
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

        # TaskHandler periodically cleans subprocess sessions
        # by checking connected socket IDs.
        logger.debug(Notice('diagnostic.ws_recv.client_resources_cleaned_up', value0=socket_id))
