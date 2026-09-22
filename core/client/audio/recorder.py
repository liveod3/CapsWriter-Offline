# coding: utf-8
"""
音频录制模块

提供 AudioRecorder 类用于管理录音会话，包括开始录音、
发送音频数据到服务端、结束录音等功能。
"""

from __future__ import annotations

from core.i18n import Notice, tr

import asyncio
import base64
import time
import uuid
from typing import TYPE_CHECKING, Optional

import numpy as np
import websockets

from config_client import ClientConfig as Config
from core.client.state import console
from core.client.audio.file_manager import AudioFileManager
from core.client.audio.capture import CaptureSession
from core.client.audio.file_writer import AsyncAudioWriter
from core.client.connection import WebSocketManager
from core.protocol import AudioMessage
from core.client.dictation_lifecycle import (
    MAX_PENDING_DICTATIONS, DictationSendError, cancel_dictation,
    close_dictation_connection, dictation_timeouts,
)
from core.client.transcribe.lifecycle import complete_cleanup
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from core.client.app import CapsWriterClient

# 日志记录器


class AudioRecorder:
    """
    音频录制器
    
    管理一次完整的录音会话，包括：
    - 从音频流接收数据
    - 可选地保存到本地文件
    - 将音频数据发送到识别服务端
    """
    
    def __init__(self, app: CapsWriterClient):
        """
        初始化录制器
        
        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self.task_id: str = str(uuid.uuid4())
        self._file_manager: Optional[AudioFileManager] = None
        self._start_time: float = 0.0
        self._duration: float = 0.0
        self._cache: list = []
        self._context = ''
        self._writer = None
        self._websocket = None
        self._io_timeout, self._result_timeout = dictation_timeouts(Config)

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    @property
    def _ws_manager(self) -> WebSocketManager:
        """快捷访问桥接到 app.ws"""
        return self.app.ws

    async def _create_recording_file(self, channels):
        """An unwritable destination disables this archive, preserving dictation."""
        try:
            path, _ = await self._writer.call(
                self._file_manager.create, channels, self._start_time)
        except OSError as exc:
            writer, self._writer = self._writer, None
            await writer.close(abort=True)
            logger.warning(Notice('diagnostic.recorder.recording_storage_unavailable_error'), type(exc).__name__,
                           extra={'console_handled': True})
            console.print(tr('audio.save_failed'), markup=False)
            from core.ui import show_status_hint
            show_status_hint(tr('audio.storage_unavailable'), duration_ms=3500)
            return None
        self.state.register_audio_file(self.task_id, path)
        return path
    
    async def _send_message(self, message: AudioMessage) -> None:
        """Bound every upload and retain the connection used for this operation."""
        message.supports_task_errors = True
        if not self._ws_manager.is_connected:
            raise DictationSendError('Disconnected')
        websocket = self.state.websocket
        if self._websocket is not None and websocket is not self._websocket:
            raise DictationSendError('ConnectionChanged')
        self._websocket = websocket
        if message.is_final:
            self.state.dictation_deadlines[self.task_id] = time.monotonic() + self._result_timeout
        try:
            success = await asyncio.wait_for(self._ws_manager.send(message), self._io_timeout)
            if not success:
                raise DictationSendError('SendRejected')
        except asyncio.CancelledError:
            await complete_cleanup(close_dictation_connection(self.state, websocket))
            raise
        except Exception as exc:
            await complete_cleanup(close_dictation_connection(self.state, websocket))
            raise DictationSendError(type(exc).__name__) from exc
    
    async def record_and_send(self, capture=None) -> None:
        """
        录音并发送数据
        
        从队列中读取音频数据，保存到文件（如果启用），
        并发送到服务端进行识别。
        """
        # Freeze the input source for this recorder, including while it drains
        # after a new recording has already claimed the microphone.
        input_queue = capture if capture is not None else self.state.queue_in
        completed = False
        upload_done = asyncio.Event()
        try:
            if len(self.state.dictation_uploads) >= MAX_PENDING_DICTATIONS:
                raise DictationSendError('PendingDictationLimit')
            self.state.dictation_uploads[self.task_id] = upload_done
            # ID 在创建录音器时固定，快捷键结束录音即可用它显示转写状态。
            logger.debug(Notice('diagnostic.recorder.recording_task_created_task', value0=self.task_id))
            
            self._start_time = 0.0
            self._duration = 0.0
            self._cache = []
            
            # 音频文件管理
            file_path = None
            if Config.save_audio:
                self._file_manager = await asyncio.to_thread(AudioFileManager)
                self._writer = AsyncAudioWriter(self._file_manager)
            
            # 从队列读取数据
            while task := await input_queue.get():
                if capture is None:
                    input_queue.task_done()
                if task['type'] == 'cancel':
                    raise asyncio.CancelledError
                if task['type'] == 'overflow':
                    raise RuntimeError('CaptureBufferOverflow')
                
                if task['type'] == 'begin':
                    self._start_time = task['time']
                    from core.client.caret_context import asr_reference
                    target = task.get('target_window', 0)
                    context = await self.app.caret_context.capture(target)
                    self._context = asr_reference(context)
                    self.state.task_contexts[self.task_id] = (context, target)
                    logger.debug(Notice('diagnostic.recorder.recording_started_timestamp', value0=self._start_time))
                    
                elif task['type'] == 'data':
                    # 在阈值之前积攒音频数据
                    if task['time'] - self._start_time < Config.threshold:
                        if len(self._cache) >= CaptureSession.MAX_PENDING_BLOCKS:
                            raise RuntimeError('CaptureBufferOverflow')
                        self._cache.append(task['data'])
                        continue
                    
                    # 创建音频文件
                    if self._writer and file_path is None:
                        file_path = await self._create_recording_file(task['data'].shape[1])
                    
                    # 获取音频数据
                    if self._cache:
                        self._cache.append(task['data'])
                        data = np.concatenate(self._cache)
                        self._cache.clear()
                    else:
                        data = task['data']
                    
                    # 保存音频至本地文件
                    self._duration += len(data) / 48000
                    if self._writer:
                        await self._writer.call(self._file_manager.write, data)
                    
                    # 发送音频数据用于识别
                    message = AudioMessage(
                        task_id=self.task_id,
                        source='mic',
                        data=base64.b64encode(
                            np.mean(data[::3], axis=1).tobytes()
                        ).decode('utf-8'),
                        is_final=False,
                        time_start=self._start_time,
                        seg_duration=Config.mic_seg_duration,
                        seg_overlap=Config.mic_seg_overlap,
                        context=self._context,
                        language=Config.language,
                    )
                    await self._send_message(message)
                    
                elif task['type'] == 'finish':
                    # 如果有缓存的数据未发送，先发送缓存
                    if self._cache:
                        data = np.concatenate(self._cache)
                        self._cache.clear()

                        # 短录音可能在阈值前就结束，此时需要先创建文件再写入
                        if self._writer and file_path is None:
                            file_path = await self._create_recording_file(data.shape[1])
                        
                        self._duration += len(data) / 48000
                        if self._writer:
                            await self._writer.call(self._file_manager.write, data)

                        message = AudioMessage(
                            task_id=self.task_id,
                            source='mic',
                            data=base64.b64encode(
                                np.mean(data[::3], axis=1).tobytes()
                            ).decode('utf-8'),
                            is_final=False,
                            time_start=self._start_time,
                            seg_duration=Config.mic_seg_duration,
                            seg_overlap=Config.mic_seg_overlap,
                            context=self._context,
                            language=Config.language,
                        )
                        await self._send_message(message)

                    # 完成写入本地文件
                    if self._writer:
                        await self._writer.close()
                        logger.debug(Notice('diagnostic.recorder.audio_file_writing_completed'))
                    
                    console.print(
                        tr('audio.duration', value0=self._duration)
                    )
                    logger.info(Notice('diagnostic.recorder.recording_task_completed_task_duration_s', value0=self.task_id, value1=self._duration))
                    
                    # 告诉服务端音频片段结束了
                    message = AudioMessage(
                        task_id=self.task_id,
                        source='mic',
                        data='',
                        is_final=True,
                        time_start=self._start_time,
                        seg_duration=Config.mic_seg_duration,
                        seg_overlap=Config.mic_seg_overlap,
                        context=self._context,
                        language=Config.language,
                    )
                    await self._send_message(message)
                    completed = True
                    break
                    
        except asyncio.CancelledError:
            self.app.progress.finish(self.task_id)
            self.state.task_contexts.pop(self.task_id, None)
            raise
        except Exception as e:
            self.app.progress.finish(self.task_id)
            self.state.task_contexts.pop(self.task_id, None)
            logger.error(Notice('diagnostic.recorder.recording_task_failed_task_error'),
                         self.task_id[:8], type(e).__name__)
            raise
        finally:
            async def cleanup():
                self._cache.clear()
                if capture is not None:
                    capture.cancel()
                cancel_remote = not completed and self.task_id in self.state.dictation_uploads
                if not completed:
                    self.state.dictation_deadlines.pop(self.task_id, None)
                    self.state.dictation_uploads.pop(self.task_id, None)
                    self.state.pop_audio_file(self.task_id)
                upload_done.set()
                try:
                    if self._writer:
                        await self._writer.close(abort=not completed)
                finally:
                    if cancel_remote:
                        await cancel_dictation(self.state, self._websocket, self.task_id)
            await complete_cleanup(cleanup())
    
    def get_file_manager(self) -> Optional[AudioFileManager]:
        """获取当前的文件管理器"""
        return self._file_manager
