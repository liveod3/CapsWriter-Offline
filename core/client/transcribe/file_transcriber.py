# coding: utf-8
"""
文件转录模块

提供 FileTranscriber 类用于将音视频文件转录为字幕。
"""

from __future__ import annotations

import asyncio
import base64
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TextColumn,
)
from rich.text import Text

from config_client import ClientConfig as Config
from core.client.state import console
from core.client.connection import CommunicationError, WebSocketManager
from core.constants import AudioFormat
from core.protocol import AudioMessage, RecognitionMessage
from .media_tool import MediaTool
from .result_handler import ResultHandler
from .lifecycle import complete_cleanup, open_process, positive_timeout, reap_process
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from core.client.app import CapsWriterClient


class MediaDecodeError(RuntimeError):
    """The decoder exited without completing a usable audio stream."""


def format_duration(seconds: float) -> str:
    """将秒数格式化为适合终端统计信息的紧凑时长。"""
    seconds = max(0.0, seconds)
    hours, remainder = divmod(int(seconds + 0.5), 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    if hours:
        return f'{hours:d}:{minutes:02d}:{whole_seconds:02d}'
    return f'{minutes:02d}:{whole_seconds:02d}'


@dataclass(frozen=True)
class TranscriptionSummary:
    """单个文件转写完成后的终端统计信息。"""

    audio_duration: float
    elapsed: float
    text_length: int
    output_paths: tuple[Path, ...]
    sequence: int

    @property
    def speed_ratio(self) -> float:
        """音频长度 / 转写耗时；1.0 表示实时速度。"""
        return self.audio_duration / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def rtf(self) -> float:
        """标准实时因子（转写耗时 / 音频长度），越小越快。"""
        return self.elapsed / self.audio_duration if self.audio_duration > 0 else 0.0


@dataclass
class ProgressEstimator:
    """根据累计与最近处理速度平滑估算实时速度和 ETA。"""

    started_at: float
    completed: float = 0.0
    last_completed: float = 0.0
    last_updated_at: float | None = None
    smoothed_speed: float = 0.0
    eta_deadline: float | None = None

    def update(
        self,
        completed: float,
        total: float | None,
        *,
        now: float | None = None,
    ) -> None:
        now = time.perf_counter() if now is None else now
        completed = max(0.0, completed)
        elapsed = max(now - self.started_at, 1e-6)
        overall_speed = completed / elapsed

        previous_at = self.last_updated_at or self.started_at
        delta_time = max(now - previous_at, 1e-6)
        delta_audio = max(0.0, completed - self.last_completed)
        recent_speed = delta_audio / delta_time if delta_audio else overall_speed

        # 累计速度抵抗单个分片抖动，最近速度及时跟随模型负载变化；再用
        # EWMA 抑制 ETA 在相邻分片之间大幅跳动。
        measurement = overall_speed * 0.65 + recent_speed * 0.35
        if self.smoothed_speed > 0:
            lower = self.smoothed_speed / 3
            upper = self.smoothed_speed * 3
            measurement = min(max(measurement, lower), upper)
            self.smoothed_speed = self.smoothed_speed * 0.7 + measurement * 0.3
        else:
            self.smoothed_speed = measurement

        self.completed = completed
        self.last_completed = completed
        self.last_updated_at = now
        if total is not None and self.smoothed_speed > 0:
            remaining = max(0.0, total - completed)
            self.eta_deadline = now + remaining / self.smoothed_speed
        else:
            self.eta_deadline = None

    def live_speed(self, *, now: float | None = None) -> float:
        """返回已确认音频时长 / 已耗时，随终端刷新实时更新。"""
        now = time.perf_counter() if now is None else now
        elapsed = max(now - self.started_at, 1e-6)
        return self.completed / elapsed

    def eta_seconds(self, *, now: float | None = None) -> float | None:
        """返回平滑 ETA；分片回报之间按墙钟时间连续倒计时。"""
        if self.eta_deadline is None:
            return None
        now = time.perf_counter() if now is None else now
        return max(0.0, self.eta_deadline - now)


class LiveMetricsColumn(ProgressColumn):
    """渲染动态 ETA 和实时倍速。"""

    def render(self, task) -> Text:
        estimator: ProgressEstimator = task.fields['estimator']
        now = time.perf_counter()
        elapsed = max(0.0, now - estimator.started_at)
        eta = estimator.eta_seconds(now=now)
        eta_text = format_duration(eta) if eta is not None else '计算中'
        speed = estimator.live_speed(now=now)
        speed_text = f'{speed:.2f}×' if speed > 0 else '计算中'
        return Text.from_markup(
            f'   [ui.label]耗时[/] [ui.value]{format_duration(elapsed)}[/]    '
            f'[ui.label]ETA[/] [ui.value]{eta_text}[/]    '
            f'[ui.label]实时速度[/] [ui.accent]{speed_text}[/]'
        )


async def read_fixed_chunk(reader: asyncio.StreamReader, chunk_size: int) -> bytes:
    """从异步管道累计读取一个定长块；到达 EOF 时返回最后一个不足定长的块。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正数")

    data = bytearray()
    while len(data) < chunk_size:
        part = await reader.read(chunk_size - len(data))
        if not part:
            break
        data.extend(part)
    return bytes(data)


class FileTranscriber:
    """
    文件转录器
    
    协调转录流程：
    1. 检查环境与文件
    2. 调用 MediaTool 提取音频
    3. 通过 WebSocket 发送数据
    4. 调用 ResultHandler 处理结果
    """
    
    def __init__(
        self,
        app: CapsWriterClient,
        file: Path,
        *,
        output_formats: frozenset[str],
    ):
        """
        初始化文件转录器
        
        Args:
            app: 客户端 App 实例
            file: 要转录的文件路径
            output_formats: 本次任务需要保存的结果格式
        """
        self.app = app
        self.file = file
        self.output_formats = output_formats
        self.task_id = str(uuid.uuid4())
        self.failure_code = None
        self._send_complete = asyncio.Event()
        self._io_timeout = positive_timeout(Config, 'file_io_timeout', 60.0)
        self._result_timeout = positive_timeout(Config, 'file_result_timeout', 600.0)
        self._audio_duration: float = 0.0
        self._decoded_duration: float = 0.0
        self._started_at: float | None = None
        self.summary: TranscriptionSummary | None = None
        self._progress: Progress | None = None
        self._progress_task_id: int | None = None
        self._progress_estimator: ProgressEstimator | None = None
        try:
            max_inflight = max(1, int(getattr(Config, 'file_max_inflight_chunks', 4)))
        except (TypeError, ValueError):
            max_inflight = 4
        self._send_window = asyncio.BoundedSemaphore(max_inflight)

    def _start_progress(self) -> None:
        """启动单行实时进度；非交互输出保持安静，避免重定向日志膨胀。"""
        if not console.is_terminal or self._progress is not None:
            return
        self._progress = Progress(
            SpinnerColumn(style='ui.accent'),
            TextColumn('[ui.accent]转写[/]'),
            BarColumn(
                bar_width=20,
                complete_style='ui.progress',
                finished_style='ui.success',
                pulse_style='ui.secondary',
            ),
            TextColumn(
                '[ui.label]进度[/] [ui.value]{task.percentage:.1f}%[/]'
            ),
            TextColumn(
                '   [ui.label]全部/已处理/待处理[/] '
                '[ui.value]{task.fields[total_audio]}/'
                '{task.fields[processed]}/{task.fields[remaining]}[/]'
            ),
            LiveMetricsColumn(),
            console=console,
            refresh_per_second=8,
        )
        self._progress.start()
        total = self._audio_duration if self._audio_duration > 0 else None
        started_at = self._started_at or time.perf_counter()
        self._progress_estimator = ProgressEstimator(started_at=started_at)
        self._progress_task_id = self._progress.add_task(
            '转写',
            total=total,
            total_audio=(format_duration(total) if total is not None else '--:--'),
            processed='00:00',
            remaining=(format_duration(total) if total is not None else '--:--'),
            estimator=self._progress_estimator,
        )

    def _update_progress(self, processed: float, *, finished: bool = False) -> None:
        """以服务端确认的已处理音频时长更新进度。"""
        if self._progress is None or self._progress_task_id is None:
            return
        total = self._audio_duration if self._audio_duration > 0 else None
        if finished:
            total = total or max(processed, self._decoded_duration)
            completed = total or processed
        else:
            if total is not None and processed > total:
                total = processed
            completed = min(processed, total) if total is not None else processed
        displayed = completed if finished else processed
        remaining_seconds = max(0.0, total - displayed) if total is not None else None
        if self._progress_estimator is not None:
            self._progress_estimator.update(displayed, total)
        self._progress.update(
            self._progress_task_id,
            completed=completed,
            total=total,
            total_audio=(format_duration(total) if total is not None else '--:--'),
            processed=format_duration(displayed),
            remaining=(
                format_duration(remaining_seconds)
                if remaining_seconds is not None
                else '--:--'
            ),
            refresh=True,
        )

    def _stop_progress(self, *, discard=False) -> None:
        if self._progress is not None:
            self._progress.live.transient = discard
            self._progress.stop()
            self._progress = None
            self._progress_task_id = None
            self._progress_estimator = None

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    @property
    def _ws_manager(self) -> 'WebSocketManager':
        """快捷访问桥接到 app.ws"""
        return self.app.ws
    
    async def check(self) -> bool:
        """检查转录条件"""
        # 检查文件是否存在
        if not self.file.exists():
            self.failure_code = 'missing_file'
            logger.error('Input file not found', extra={'console_handled': True})
            return False

        # 检查媒体工具环境 (FFmpeg)
        if not MediaTool.check_environment():
            self.failure_code = 'decoder_unavailable'
            return False

        # 检查服务端连接
        if not await asyncio.wait_for(
            self._ws_manager.connect(announce=False), self._io_timeout
        ):
            self.failure_code = 'connection_failed'
            logger.error('File connection failed', extra={'console_handled': True})
            return False
        
        
        return True
    
    async def send(self) -> bool:
        """发送音频数据到服务端 (异步流式处理)"""
        
        # 1. 预先获取时长
        self._audio_duration = await MediaTool.get_audio_duration(self.file)
        
        logger.info('File transcription started: task=%s', self.task_id[:8])
        time_start = time.time()
        self._started_at = time.perf_counter()
        self._start_progress()
        
        # 2. 启动 FFmpeg 进程
        ffmpeg_cmd = MediaTool.build_ffmpeg_cmd(self.file)
        
        process = None
        try:
            process = await open_process(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            # StreamReader.read(n) 不保证一次返回 n 字节。必须在客户端先累计出
            # 一个完整识别分片，否则“在途消息数”会按管道碎片消耗，并在服务端
            # 凑够首个识别片段前形成相互等待。
            chunk_size = AudioFormat.seconds_to_bytes(Config.file_seg_duration)
            bytes_sent = 0
            progress = 0.0
            
            while True:
                data = await asyncio.wait_for(
                    read_fixed_chunk(process.stdout, chunk_size), self._io_timeout)
                if not data:
                    break
                
                bytes_sent += len(data)
                progress = bytes_sent / 4 / 16000
                self._decoded_duration = progress

                message = AudioMessage(
                    task_id=self.task_id,
                    source='file',
                    supports_task_errors=True,
                    data=base64.b64encode(data).decode('utf-8'),
                    is_final=False,
                    time_start=time_start,
                    seg_duration=Config.file_seg_duration,
                    seg_overlap=Config.file_seg_overlap,
                    context='',
                    language=Config.language,
                )
                await asyncio.wait_for(self._send_window.acquire(), self._result_timeout)
                try:
                    if not await asyncio.wait_for(
                        self._ws_manager.send(message), self._io_timeout
                    ):
                        raise ConnectionError("消息发送失败，连接可能已断开")
                except Exception:
                    self._send_window.release()
                    raise

            returncode = await asyncio.wait_for(process.wait(), self._io_timeout)
            if returncode != 0:
                raise MediaDecodeError(f'DecoderExit:{returncode}')

            # 发送结束标志
            final_message = AudioMessage(
                task_id=self.task_id,
                source='file',
                supports_task_errors=True,
                data='',
                is_final=True,
                time_start=time_start,
                seg_duration=Config.file_seg_duration,
                seg_overlap=Config.file_seg_overlap,
                context='',
                language=Config.language,
            )
            if not await asyncio.wait_for(
                self._ws_manager.send(final_message), self._io_timeout
            ):
                raise ConnectionError("结束标志发送失败")
            self._send_complete.set()
            
            if self._audio_duration == 0:
                self._audio_duration = progress

            logger.debug("音频数据发送完成")
            return True
            
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.failure_code is None:
                if isinstance(exc, asyncio.TimeoutError):
                    self.failure_code = 'timeout'
                elif isinstance(exc, (CommunicationError, ConnectionError)):
                    self.failure_code = 'connection_failed'
                elif isinstance(exc, MediaDecodeError):
                    self.failure_code = 'decode_failed'
                elif isinstance(exc, FileNotFoundError):
                    self.failure_code = 'decoder_unavailable'
                else:
                    self.failure_code = 'unexpected'
            logger.error('File send failed: task=%s error=%s',
                         self.task_id[:8], type(exc).__name__,
                         extra={'console_handled': True})
            return False
        finally:
            if process is not None:
                await complete_cleanup(reap_process(process))
    
    async def receive(self) -> bool:
        """接收转录结果"""
        message = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._result_timeout
        processed = 0.0
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                msg = await asyncio.wait_for(self._ws_manager.receive(), remaining)
                if not msg:
                    if self.failure_code is None:
                        self.failure_code = 'connection_failed'
                    return False
                # Stale results must not replenish another task's send window,
                # reset its deadline or create an output file for it.
                if msg.task_id != self.task_id:
                    continue
                if msg.error_code:
                    self.failure_code = 'recognition_failed'
                    logger.error('Server rejected file task: task=%s code=%s',
                                 self.task_id[:8], msg.error_code,
                                 extra={'console_handled': True})
                    return False
                if not math.isfinite(msg.duration) or msg.duration < 0:
                    raise ValueError('InvalidRecognitionDuration')
                if msg.duration > processed:
                    processed = msg.duration
                    deadline = loop.time() + self._result_timeout
                    try:
                        self._send_window.release()
                    except ValueError:
                        pass
                
                self._update_progress(msg.duration, finished=msg.is_final)
                if msg.is_final:
                    # Do not save a final received while the sender is failing.
                    await asyncio.wait_for(self._send_complete.wait(), self._io_timeout)
                    message = msg # 保持变量名兼容后续调用
                    break
        except Exception as exc:
            if self.failure_code is None:
                if isinstance(exc, asyncio.TimeoutError):
                    self.failure_code = 'timeout'
                elif isinstance(exc, (CommunicationError, ConnectionError)):
                    self.failure_code = 'connection_failed'
                else:
                    self.failure_code = 'invalid_result'
            logger.error('File receive failed: task=%s error=%s',
                         self.task_id[:8], type(exc).__name__,
                         extra={'console_handled': True})
            return False

        if message is None:
            return False

        # 调用结果处理器进行保存和格式化
        try:
            text_display, sequence, output_paths = ResultHandler.save_results(
                self.file,
                message,
                output_formats=self.output_formats,
            )
        except Exception:
            self.failure_code = 'output_failed'
            raise
        self._stop_progress()

        if sequence > 1:
            console.print(
                f'[ui.warning]▲ 同名结果已存在，本次使用编号 ({sequence})；'
                '未覆盖既有文件[/]'
            )

        elapsed = (
            time.perf_counter() - self._started_at
            if self._started_at is not None
            else max(0.0, message.time_complete - message.time_start)
        )
        audio_duration = self._decoded_duration or self._audio_duration or message.duration
        self.summary = TranscriptionSummary(
            audio_duration=audio_duration,
            elapsed=elapsed,
            text_length=len(text_display),
            output_paths=tuple(output_paths),
            sequence=sequence,
        )
        
        logger.info(
            'File transcription completed: task=%s duration=%.2f elapsed=%.2f '
            'speed=%.2f rtf=%.3f chars=%d sequence=%d outputs=%d',
            self.task_id[:8], audio_duration, elapsed, self.summary.speed_ratio,
            self.summary.rtf, len(text_display), sequence, len(output_paths),
        )
        return True


    async def close(self) -> None:
        """释放资源，关闭 WebSocket 连接"""
        self._stop_progress(discard=self.summary is None)
        websocket = getattr(self.state, 'websocket', None)
        try:
            await asyncio.wait_for(self._ws_manager.close(), 5.0)
        except Exception as exc:
            logger.warning('File connection cleanup failed: %s', type(exc).__name__)
            if websocket is not None:
                transport = getattr(websocket, 'transport', None)
                if transport is not None:
                    transport.abort()
                if self.state.websocket is websocket:
                    self.state.websocket = None
