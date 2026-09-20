# coding: utf-8
"""
音频录制模块

提供 AudioRecorder 类用于管理录音会话，包括开始录音、
发送音频数据到服务端、结束录音等功能。
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from typing import TYPE_CHECKING, Optional

import numpy as np
import websockets

from config_client import ClientConfig as Config
from core.client.state import console
from core.client.audio.file_manager import AudioFileManager
from core.client.audio.capture import CaptureSession
from core.client.connection import WebSocketManager
from core.protocol import AudioMessage
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

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    @property
    def _ws_manager(self) -> WebSocketManager:
        """快捷访问桥接到 app.ws"""
        return self.app.ws
    
    async def _send_message(self, message: AudioMessage) -> None:
        """发送消息到服务端"""
        if not self._ws_manager.is_connected:
            if message.is_final:
                self.app.progress.finish(message.task_id)
                self.state.task_contexts.pop(message.task_id, None)
                self.state.pop_audio_file(message.task_id)
                console.print('[ui.error]✗ 服务端未连接，录音未发送[/]\n')
                logger.warning("服务端未连接，无法发送音频数据")
            return
        
        # 使用 WebSocketManager 发送协议消息
        success = await self._ws_manager.send(message)
        if not success and message.is_final:
            self.app.progress.finish(message.task_id)
            self.state.task_contexts.pop(message.task_id, None)
            self.state.pop_audio_file(message.task_id)
            # 具体错误日志由 WebSocketManager 记录
    
    async def record_and_send(self, capture=None) -> None:
        """
        录音并发送数据
        
        从队列中读取音频数据，保存到文件（如果启用），
        并发送到服务端进行识别。
        """
        # Freeze the input source for this recorder, including while it drains
        # after a new recording has already claimed the microphone.
        input_queue = capture if capture is not None else self.state.queue_in
        try:
            # ID 在创建录音器时固定，快捷键结束录音即可用它显示转写状态。
            logger.debug(f"创建录音任务，任务ID: {self.task_id}")
            
            self._start_time = 0.0
            self._duration = 0.0
            self._cache = []
            
            # 音频文件管理
            file_path = None
            if Config.save_audio:
                self._file_manager = AudioFileManager()
            
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
                    if len(self.state.task_contexts) >= 64:
                        self.state.task_contexts.pop(next(iter(self.state.task_contexts)))
                    self.state.task_contexts[self.task_id] = (context, target)
                    logger.debug(f"录音开始，时间戳: {self._start_time}")
                    
                elif task['type'] == 'data':
                    # 在阈值之前积攒音频数据
                    if task['time'] - self._start_time < Config.threshold:
                        if len(self._cache) >= CaptureSession.MAX_PENDING_BLOCKS:
                            raise RuntimeError('CaptureBufferOverflow')
                        self._cache.append(task['data'])
                        continue
                    
                    # 创建音频文件
                    if Config.save_audio and self._file_manager and file_path is None:
                        file_path, _ = self._file_manager.create(
                            task['data'].shape[1],
                            self._start_time
                        )
                        self.state.register_audio_file(self.task_id, file_path)
                        logger.debug(f"创建音频文件: {file_path}")
                    
                    # 获取音频数据
                    if self._cache:
                        self._cache.append(task['data'])
                        data = np.concatenate(self._cache)
                        self._cache.clear()
                    else:
                        data = task['data']
                    
                    # 保存音频至本地文件
                    self._duration += len(data) / 48000
                    if Config.save_audio and self._file_manager:
                        self._file_manager.write(data)
                    
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
                        if Config.save_audio and self._file_manager and file_path is None:
                            file_path, _ = self._file_manager.create(
                                data.shape[1],
                                self._start_time
                            )
                            self.state.register_audio_file(self.task_id, file_path)
                            logger.debug(f"创建音频文件(短录音): {file_path}")
                        
                        self._duration += len(data) / 48000
                        if Config.save_audio and self._file_manager:
                            self._file_manager.write(data)

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
                    if Config.save_audio and self._file_manager:
                        self._file_manager.finish()
                        logger.debug("完成音频文件写入")
                    
                    console.print(
                        f'[ui.label]录音[/]  [ui.value]{self._duration:.2f}s[/]'
                    )
                    logger.info(f"录音任务完成，任务ID: {self.task_id}, 时长: {self._duration:.2f}s")
                    
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
                    break
                    
        except asyncio.CancelledError:
            self.app.progress.finish(self.task_id)
            self.state.task_contexts.pop(self.task_id, None)
            raise
        except Exception as e:
            self.app.progress.finish(self.task_id)
            self.state.task_contexts.pop(self.task_id, None)
            logger.error(f"录音任务错误: {e}", exc_info=True)
            raise
        finally:
            self._cache.clear()
            if capture is not None:
                capture.cancel()
            if self._file_manager:
                self._file_manager.finish()
    
    def get_file_manager(self) -> Optional[AudioFileManager]:
        """获取当前的文件管理器"""
        return self._file_manager
