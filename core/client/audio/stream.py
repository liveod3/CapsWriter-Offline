# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import time
import threading
from functools import partial
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

from config_client import ClientConfig as Config
from core.client.state import console
from core.ui.recording_indicator import show_status_hint
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import CapsWriterClient



class AudioStreamManager:
    """
    音频流管理器
    
    负责管理音频输入流的生命周期，包括：
    - 检测和选择音频设备
    - 创建和启动音频流
    - 处理音频数据回调
    - 流的重启和关闭
    - 在空闲时通过重载 PortAudio 动态监控默认设备变动
    
    Attributes:
        state: 客户端状态实例
        sample_rate: 采样率（默认 48000Hz）
        block_duration: 每个数据块的时长（秒，默认 0.05s）
    """
    
    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.05  # 50ms
    
    def __init__(self, app: CapsWriterClient):
        """
        初始化音频流管理器
        
        Args:
            app: 客户端 App 实例
        """
        self.app = app
        # 生命周期操作可能嵌套调用（例如 reopen() 内部调用 stop()/start()）。
        self._stream_lock = threading.RLock()
        self._ready_event = threading.Event()
        self._channels = 1
        self._running = False  # 标志是否应该运行
        self._last_input_device = None
        self._monitor_thread = None
        self._monitor_running = False

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    @staticmethod
    def _get_input_device_selector():
        """获取输入设备配置；空字符串与旧配置均回退到系统默认设备。"""
        selector = getattr(Config, 'input_device', None)
        if isinstance(selector, str):
            selector = selector.strip()
            return selector or None
        return selector

    def get_ready_event(self) -> threading.Event:
        """返回当前音频流的就绪事件，供非阻塞 UI 等待使用。"""
        return self._ready_event

    def is_ready(self, ready_event: Optional[threading.Event] = None) -> bool:
        """当前音频流是否已收到首个音频回调。"""
        event = ready_event or self._ready_event
        return self._running and event is self._ready_event and event.is_set()

    def _commit_input_device(self, device_name: str) -> None:
        """记录成功选择的输入设备，并在发生切换时统一提示。"""
        previous_device = self._last_input_device
        self._last_input_device = device_name
        if not previous_device or previous_device == device_name:
            return

        message = f'输入设备已切换：{device_name}'
        logger.info(f"输入音频设备已切换: {previous_device} -> {device_name}")
        console.print(
            f'\n[ui.warning]● 输入设备已切换[/]  '
            f'[ui.value]{previous_device}[/] [ui.secondary]→[/] '
            f'[ui.success]{device_name}[/]'
        )
        show_status_hint(message, duration_ms=2600, dot_color='#F59E0B')

    def _handle_monitored_device(self, device_name: str) -> None:
        """处理监控线程观察到的设备，挂起时只更新状态，不重新占用麦克风。"""
        if not device_name:
            return

        if self._last_input_device and device_name != self._last_input_device:
            if self.state.dictation_paused:
                self._commit_input_device(device_name)
            else:
                logger.info(
                    f"监控线程检测到输入音频设备变更，准备重开音频流: "
                    f"{self._last_input_device} -> {device_name}"
                )
                self.reopen()
            return

        # 先前重开失败时，在设备重新可用后继续尝试恢复音频流。
        if not self._running and not self.state.dictation_paused:
            self.start(silent=True)

    def _query_monitored_input_device(self):
        """查询监控目标；挂起且无流时先刷新 PortAudio 的设备枚举缓存。"""
        if self.state.dictation_paused and not self._running:
            try:
                sd._terminate()
                sd._initialize()
            except Exception as e:
                logger.debug(f"挂起期间刷新 PortAudio 设备枚举失败: {e}")

        return sd.query_devices(
            device=self._get_input_device_selector(),
            kind='input'
        )
    
    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags,
        ready_event: Optional[threading.Event] = None,
    ) -> None:
        """
        音频数据回调函数
        
        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
        # stream.start() 返回不代表硬件已经开始交付数据；首个回调才是真正就绪。
        event = ready_event or self._ready_event
        if not event.is_set():
            event.set()
            logger.info("音频设备已就绪：收到首个音频数据块")

        # 只在录音状态时处理数据
        if not self.state.recording:
            return
        
        import asyncio
        
        # 将数据放入队列
        if self.app.loop and self.state.queue_in:
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'data',
                    'time': time.time(),
                    'data': indata.copy(),
                }),
                self.app.loop
            )
    
    def _on_stream_finished(self) -> None:
        """音频流结束回调"""
        if not threading.main_thread().is_alive():
            return
        if not self._running:
            return
        
        logger.info("音频流意外结束，正在尝试重启...")
        # PortAudio 的 finished_callback 运行在 PortAudio 内部线程上，
        # 禁止在此线程内直接调用 stream.close()（会死锁）。
        # 改为新建守护线程异步执行重启，立即返回回调。
        threading.Thread(target=self.reopen, daemon=True, name="stream-reopen").start()

    def _device_monitor_loop(self) -> None:
        """后台静默监控系统默认输入设备变化的循环"""
        while self._monitor_running:
            time.sleep(4.0)  # 每 4 秒检测一次硬件状态
            
            # 如果用户当前正在录音说话，绝对不要打断当前的音频流。
            # 挂起期间仍可只读查询默认设备，但不会重新打开麦克风。
            if self.state.recording:
                continue
                
            try:
                # 持锁查询，防止与 reopen() 内的 PortAudio 重初始化并发访问
                with self._stream_lock:
                    device = self._query_monitored_input_device()
                current_device_name = device.get('name')
                self._handle_monitored_device(current_device_name)

            except Exception as e:
                logger.debug(f"后台硬件监听循环异常: {e}")
                # 确保在任何意外错误后，底层的录音流一定能够被拉起
                if (not self._running) and (not self.state.dictation_paused):
                    self.start(silent=True)

    def start(self, silent: bool = False, force: bool = False) -> Optional[sd.InputStream]:
        """在线程安全的生命周期锁内启动音频流。"""
        with self._stream_lock:
            return self._start_locked(silent=silent, force=force)

    def _start_locked(self, silent: bool = False, force: bool = False) -> Optional[sd.InputStream]:
        """
        启动音频流
        
        Args:
            silent: 是否静默启动（不向控制台打印设备选择信息）
            
        Returns:
            创建的音频输入流，如果失败返回 None
        """
        if self._running:
            logger.debug("音频流已在运行，跳过启动")
            return self.state.stream

        if self.state.dictation_paused and not force:
            logger.debug("当前处于听写挂起状态，跳过启动音频流")
            return None
            
        # 检测音频设备
        device_selector = self._get_input_device_selector()
        try:
            device = sd.query_devices(device=device_selector, kind='input')
            self._channels = min(2, device['max_input_channels'])
            device_name = device.get('name', '未知设备')
            selection_mode = '系统默认' if device_selector is None else '指定配置'
            
            if not silent:
                console.print(
                    f'[ui.label]音频设备[/]  [ui.value]{device_name}[/]  '
                    f'[ui.muted]{selection_mode} · {self._channels} 声道[/]',
                    end='\n\n'
                )
            logger.info(
                f"找到音频设备: {device_name}, 声道数: {self._channels}, "
                f"选择方式: {selection_mode}"
            )
        except UnicodeDecodeError:
            logger.warning("无法获取音频设备名称（编码问题）")
        except (ValueError, sd.PortAudioError) as e:
            if device_selector is None:
                logger.error(f"未找到系统默认麦克风设备: {e}")
            else:
                logger.error(f"未找到指定麦克风设备 {device_selector!r}: {e}")
            return None
        
        # 创建音频流
        try:
            ready_event = threading.Event()
            self._ready_event = ready_event
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                device=device_selector,
                dtype="float32",
                channels=self._channels,
                callback=partial(self._audio_callback, ready_event=ready_event),
                finished_callback=self._on_stream_finished,
            )
            stream.start()
            
            self.state.stream = stream
            self._running = True
            self._commit_input_device(device_name)
            logger.debug(
                f"音频流已启动: 采样率={self.SAMPLE_RATE}, "
                f"块大小={int(self.BLOCK_DURATION * self.SAMPLE_RATE)}"
            )

            # 启动或确保硬件监听线程就绪
            if not self._monitor_running:
                self._monitor_running = True
                self._monitor_thread = threading.Thread(target=self._device_monitor_loop, daemon=True)
                self._monitor_thread.start()

            return stream
            
        except Exception as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            return None
    
    def stop(self, keep_monitor: bool = False) -> None:
        """在线程安全的生命周期锁内停止音频流。"""
        with self._stream_lock:
            self._stop_locked(keep_monitor=keep_monitor)

    def _stop_locked(self, keep_monitor: bool = False) -> None:
        """
        停止音频流
        
        Args:
            keep_monitor: 是否保持监控线程的运行标志。在重载驱动重建流时，应设为 True。
        """
        if not self._running:
            if not keep_monitor:
                self._monitor_running = False
                self._monitor_thread = None
            return
            
        self._running = False  # 标记为停止

        # 仅在需要彻底释放硬件服务时关闭后台监听线程
        if not keep_monitor:
            self._monitor_running = False
            self._monitor_thread = None

        if self.state.stream is not None:
            try:
                self.state.stream.close()
                logger.debug("音频流已停止")
            except Exception as e:
                logger.debug(f"停止音频流时发生错误: {e}")
            finally:
                self.state.stream = None
    
    def reopen(self) -> Optional[sd.InputStream]:
        """
        重新启动音频流
        
        Returns:
            新创建的音频输入流
        """
        logger.info("正在重启音频流...")
        
        with self._stream_lock:
            # 停止旧流，但指示监控线程保持运行，防止其被销毁
            self.stop(keep_monitor=True)

            # 重载 PortAudio，更新设备列表
            # 注意：不使用 sd._ffi.dlclose/dlopen 手动卸载/重载 DLL——
            # 这是私有 API，在 Windows 上行为不可靠，且存在与监控线程的竞态，
            # 可导致 access violation 崩溃（进程直接退出，无任何 Python 异常记录）。
            # sd._terminate() + sd._initialize() 足以刷新设备枚举。
            try:
                sd._terminate()
                sd._initialize()
            except Exception as e:
                logger.warning(f"重载 PortAudio 时发生警告: {e}")

            # 等待设备稳定
            time.sleep(0.1)

            # 启动新流
            return self.start()
