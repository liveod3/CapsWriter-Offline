# coding: utf-8
"""
音频文件管理模块

提供 AudioFileManager 类用于管理音频文件的创建、写入、完成和重命名。
支持 MP3（需要 FFmpeg）和 WAV 两种格式。
"""

from __future__ import annotations

from core.i18n import Notice

import re
import shutil
import tempfile
import time
import wave
import os
from threading import Event, Lock
from os import makedirs
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired
from typing import Optional, Tuple, Union

import numpy as np

from config_client import BASE_DIR, ClientConfig as Config
from .storage import recording_directory
from . import logger


# 音频文件句柄类型
AudioWriter = Union[Popen, wave.Wave_write]


class AudioFileManager:
    """
    音频文件管理器
    
    负责音频文件的完整生命周期管理：
    - 创建：根据是否安装 FFmpeg 选择 MP3 或 WAV 格式
    - 写入：将音频数据写入文件
    - 完成：关闭文件句柄
    - 重命名：根据识别文本重命名文件
    """
    
    SAMPLE_RATE = 48000
    FINISH_TIMEOUT = 5.0
    KILL_TIMEOUT = 2.0
    
    def __init__(self, *, base_dir=None):
        """初始化音频文件管理器"""
        self.file_path: Optional[Path] = None
        self.storage_root = recording_directory(Config, Path(base_dir or BASE_DIR))
        self.file_handle: Optional[AudioWriter] = None
        self.channels: int = 1
        self._aborted = Event()
        self._process_lock = Lock()
        self._ffmpeg_path = shutil.which('ffmpeg')
        self._has_ffmpeg = self._ffmpeg_path is not None
        
        if self._has_ffmpeg:
            logger.debug(Notice('diagnostic.file_manager.ffmpeg_detected_recordings_will_use_mp'))
        else:
            logger.debug(Notice('diagnostic.file_manager.ffmpeg_not_detected_recordings_will_use_wav'))
    
    def create(self, channels: int, time_start: float) -> Tuple[Path, AudioWriter]:
        """
        创建音频文件
        
        Args:
            channels: 音频声道数
            time_start: 录音开始时间戳
            
        Returns:
            (文件路径, 文件写入句柄) 元组
        """
        if self._aborted.is_set():
            raise RuntimeError('AudioWriterAborted')
        self.channels = channels
        
        # 构建目录和文件名
        local_time = time.localtime(time_start)
        time_year = time.strftime('%Y', local_time)
        time_month = time.strftime('%m', local_time)
        time_ymdhms = time.strftime("%Y%m%d-%H%M%S", local_time)
        
        folder_path = self.storage_root / time_year / time_month
        makedirs(folder_path, exist_ok=True)
        
        # 创建临时文件名
        suffix = '.mp3' if self._ffmpeg_path else '.wav'
        fd, reserved_path = tempfile.mkstemp(
            prefix=f'({time_ymdhms})', suffix=suffix, dir=folder_path)
        os.close(fd)
        file_path = Path(reserved_path)
        
        if self._ffmpeg_path:
            # 使用 FFmpeg 输出 MP3
            file_path = file_path.with_suffix('.mp3')
            ffmpeg_command = [
                self._ffmpeg_path, '-y',
                '-f', 'f32le',
                '-ar', str(self.SAMPLE_RATE),
                '-ac', str(channels),
                '-i', '-',
                '-b:a', '192k',
                str(file_path),
            ]
            try:
                file_handle = Popen(
                    ffmpeg_command,
                    stdin=PIPE,
                    stdout=DEVNULL,
                    stderr=DEVNULL,
                    bufsize=0,
                )
                logger.debug(Notice('diagnostic.file_manager.mp_recording_file_created'))
            except OSError as exc:
                logger.warning(Notice('diagnostic.file_manager.ffmpeg_startup_failed_using_wav'), type(exc).__name__)
                self._ffmpeg_path = None
                self._has_ffmpeg = False
                # Only remove the empty file reserved by this failed create.
                file_path.unlink()
                fd, reserved_path = tempfile.mkstemp(
                    prefix=f'({time_ymdhms})', suffix='.wav', dir=folder_path)
                os.close(fd)
                file_path = Path(reserved_path)

        if not self._ffmpeg_path:
            # 使用 wave 模块输出 WAV
            file_path = file_path.with_suffix('.wav')
            file_handle = wave.open(str(file_path), 'w')
            try:
                file_handle.setnchannels(channels)
                file_handle.setsampwidth(2)  # 16-bit
                file_handle.setframerate(self.SAMPLE_RATE)
            except Exception:
                file_handle.close()
                raise
            logger.debug(Notice('diagnostic.file_manager.wav_recording_file_created'))
        
        self.file_path = file_path
        with self._process_lock:
            self.file_handle = file_handle
            aborted = self._aborted.is_set()
        if aborted:
            self.abort()
            self.finish()
            raise RuntimeError('AudioWriterAborted')
        
        return file_path, file_handle
    
    def write(self, data: np.ndarray) -> None:
        """
        写入音频数据
        
        Args:
            data: 音频数据数组（float32 格式）
        """
        if self.file_handle is None:
            logger.warning(Notice('diagnostic.file_manager.cannot_write_audio_file_is_not_open'))
            return
        if self._aborted.is_set():
            raise RuntimeError('AudioWriterAborted')
        
        if isinstance(self.file_handle, Popen):
            # FFmpeg 进程
            # Unbuffered pipes may accept only part of a block.
            payload = memoryview(data.tobytes())
            while payload:
                if self._aborted.is_set():
                    raise RuntimeError('AudioWriterAborted')
                written = self.file_handle.stdin.write(payload)
                if not written:
                    raise BrokenPipeError('AudioEncoderPipeClosed')
                payload = payload[written:]
        elif isinstance(self.file_handle, wave.Wave_write):
            # WAV 文件：转换 float32 -> int16
            int_data = (data * (2**15 - 1)).astype(np.int16).tobytes()
            self.file_handle.writeframes(int_data)
    
    def finish(self) -> Optional[Path]:
        """
        完成音频文件写入
        
        Returns:
            音频文件路径
        """
        if self.file_handle is None:
            return self.file_path
        
        handle = self.file_handle
        try:
            if isinstance(handle, Popen):
                try:
                    handle.stdin.close()
                    returncode = handle.wait(timeout=self.FINISH_TIMEOUT)
                except (TimeoutExpired, OSError):
                    if handle.poll() is None:
                        handle.kill()
                    handle.wait(timeout=self.KILL_TIMEOUT)
                    raise RuntimeError('AudioEncoderFinishFailed') from None
                if returncode != 0 and not self._aborted.is_set():
                    raise RuntimeError('AudioEncoderFailed')
            elif isinstance(handle, wave.Wave_write):
                handle.close()
        finally:
            with self._process_lock:
                self.file_handle = None
        
        return self.file_path

    def abort(self) -> None:
        """Interrupt a blocked pipe write without closing its handle concurrently.

        The serial writer owns stdin and finish(); killing the child unblocks
        a write, and finish() reaps the child after that write returns.
        """
        self._aborted.set()
        with self._process_lock:
            handle = self.file_handle
        if isinstance(handle, Popen) and handle.poll() is None:
            try:
                handle.kill()
            except OSError:
                if handle.poll() is None:
                    raise
    
    def rename(self, text: str, time_start: float) -> Optional[Path]:
        """
        根据识别文本重命名音频文件
        
        Args:
            text: 识别出的文本
            time_start: 录音开始时间戳
            
        Returns:
            重命名后的文件路径，如果失败返回 None
        """
        if self.file_path is None or not self.file_path.exists():
            logger.warning(Notice('diagnostic.file_manager.audio_rename_skipped_file_unavailable'))
            return None
        
        # 构建新文件名
        time_ymdhms = time.strftime("%Y%m%d-%H%M%S", time.localtime(time_start))
        
        # 截取文本并清理非法字符
        text_clean = text[:Config.audio_name_len]
        text_clean = re.sub(r'[\\/:\"*?<>|]', ' ', text_clean)
        
        file_stem = f'({time_ymdhms}){text_clean}'
        new_path = self.file_path.with_name(file_stem + self.file_path.suffix)
        
        try:
            self.file_path.rename(new_path)
            logger.debug(Notice('diagnostic.file_manager.audio_renamed_name_chars'), len(text_clean))
            self.file_path = new_path
            return new_path
        except Exception as e:
            logger.error(Notice('diagnostic.file_manager.audio_rename_failed_error'), type(e).__name__)
            return self.file_path
