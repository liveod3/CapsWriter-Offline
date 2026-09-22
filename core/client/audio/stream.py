# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

from core.i18n import Notice, tr

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
from .portaudio_compat import refresh_devices

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
        self._shutdown = threading.Event()
        self._monitor_wakeup = threading.Event()
        self._recovery_requested = None
        self._last_recovery = 0.0

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

        message = tr('audio.changed', value0=device_name)
        logger.info(Notice('diagnostic.stream.input_audio_device_changed', value0=previous_device, value1=device_name))
        console.print(
            tr('audio.changed_console', value0=previous_device, value1=device_name)
        )
        show_status_hint(message, duration_ms=2600, dot_color='#F59E0B')

    def _handle_monitored_device(self, device_name: str) -> None:
        """处理监控线程观察到的设备，挂起时只更新状态，不重新占用麦克风。"""
        if self._shutdown.is_set() or not device_name or self.state.recording:
            return

        if self._last_input_device and device_name != self._last_input_device:
            if self.state.dictation_paused:
                self._commit_input_device(device_name)
            else:
                logger.info(
                    Notice('diagnostic.stream.input_device_change_detected_reopening_audio_stream', value0=self._last_input_device, value1=device_name)
                )
                self.reopen()
            return

        # 先前重开失败时，在设备重新可用后继续尝试恢复音频流。
        if not self._running and not self.state.dictation_paused:
            self.start(silent=True)

    def _query_monitored_input_device(self):
        """查询监控目标；挂起且无流时先刷新 PortAudio 的设备枚举缓存。"""
        if not self._running and self.state.stream is None:
            refresh_devices(sd)

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
        capture = getattr(self.state, 'capture', None)
        # stream.start() 返回不代表硬件已经开始交付数据；首个回调才是真正就绪。
        event = ready_event or self._ready_event
        if self._shutdown.is_set() or event is not self._ready_event:
            return
        if not event.is_set():
            event.set()

        # 只在录音状态时处理数据
        if not self.state.recording:
            return
        
        # A retained snapshot can only append to its own recording. finish()
        # closes that bridge before another shortcut can publish a new one.
        if capture is not None:
            capture.push_audio(indata, time.time())
    
    def _on_stream_finished(self, ready_event=None) -> None:
        """Notify the single recovery owner; never close or log in this callback."""
        event = ready_event or self._ready_event
        if (self._shutdown.is_set() or not self._running
                or event is not self._ready_event):
            return
        self._recovery_requested = event
        self._monitor_wakeup.set()

    def _cancel_interrupted_capture(self):
        """Cancel only the capture interrupted by this device failure."""
        capture = getattr(self.state, 'capture', None)
        if capture is None:
            return

        def cancel():
            with self.state.recording_lock:
                if self.state.capture is not capture:
                    return
                owner = self.state.recording_owner
                if owner is not None:
                    owner.cancel()
                    show_status_hint(tr('mic.disconnected'),
                                     duration_ms=3000, dot_color='#EF4444')
        try:
            self.app.loop.call_soon_threadsafe(cancel)
        except RuntimeError:
            pass

    def _ensure_monitor_locked(self):
        if self._shutdown.is_set():
            return
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            self._monitor_running = True
            return
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._device_monitor_loop, daemon=True, name='audio-device-monitor')
        self._monitor_thread.start()

    def _device_monitor_loop(self) -> None:
        """后台静默监控系统默认输入设备变化的循环"""
        while self._monitor_running and not self._shutdown.is_set():
            self._monitor_wakeup.wait(4.0)
            self._monitor_wakeup.clear()
            if not self._monitor_running or self._shutdown.is_set():
                break
            # Repeated backend failures must not create a hot restart loop.
            delay = 1.0 - (time.monotonic() - self._last_recovery)
            if delay > 0 and self._shutdown.wait(delay):
                break
            with self._stream_lock:
                event = self._recovery_requested
                self._recovery_requested = None
                if (event is self._ready_event and event is not None
                        and self._running and not self.state.dictation_paused):
                    self._last_recovery = time.monotonic()
                    self._cancel_interrupted_capture()
                    try:
                        self.reopen()
                    except Exception as exc:
                        logger.warning(Notice('diagnostic.stream.audio_recovery_failed'), type(exc).__name__)
                    continue
            
            # 如果用户当前正在录音说话，绝对不要打断当前的音频流。
            # 挂起期间仍可只读查询默认设备，但不会重新打开麦克风。
            if self.state.recording:
                continue
                
            try:
                # 持锁查询，防止与 reopen() 内的 PortAudio 重初始化并发访问
                with self._stream_lock:
                    if self._shutdown.is_set() or not self._monitor_running:
                        break
                    device = self._query_monitored_input_device()
                    current_device_name = device.get('name')
                    self._handle_monitored_device(current_device_name)

            except Exception as e:
                logger.debug(Notice('diagnostic.stream.hardware_monitor_loop_failed', value0=e))
                # 确保在任何意外错误后，底层的录音流一定能够被拉起
                if (not self._running) and (not self.state.dictation_paused):
                    self.start(silent=True)

    def start(self, silent: bool = False, force: bool = False) -> Optional[sd.InputStream]:
        """在线程安全的生命周期锁内启动音频流。"""
        with self._stream_lock:
            if self._shutdown.is_set():
                return None
            self._ensure_monitor_locked()
            return self._start_locked(silent=silent, force=force)

    def _start_locked(self, silent: bool = False, force: bool = False) -> Optional[sd.InputStream]:
        """
        启动音频流
        
        Args:
            silent: 是否静默启动（不向控制台打印设备选择信息）
            
        Returns:
            创建的音频输入流，如果失败返回 None
        """
        if self._shutdown.is_set():
            return None
        if self._running:
            logger.debug(Notice('diagnostic.stream.audio_stream_already_running_startup_skipped'))
            return self.state.stream

        if self.state.dictation_paused and not force:
            logger.debug(Notice('diagnostic.stream.dictation_suspended_audio_stream_startup_skipped'))
            return None
        if self.state.stream is not None:
            try:
                self._stop_locked(keep_monitor=True)
            except Exception:
                return None
            
        # 检测音频设备
        device_selector = self._get_input_device_selector()
        try:
            device = sd.query_devices(device=device_selector, kind='input')
            self._channels = min(2, device['max_input_channels'])
            device_name = device.get('name', tr('audio.unknown_device'))
            selection_mode = Notice('audio.system_default' if device_selector is None else 'audio.configured')
            
            if not silent:
                console.print(
                    tr('audio.device_console', value0=device_name, value1=selection_mode, value2=self._channels),
                    end='\n\n'
                )
            logger.info(
                Notice('diagnostic.stream.audio_device_found_channels_selection', value0=device_name, value1=self._channels, value2=selection_mode)
            )
        except UnicodeDecodeError:
            logger.warning(Notice('diagnostic.stream.could_not_decode_input_device_information'))
            return None
        except (ValueError, sd.PortAudioError) as e:
            if device_selector is None:
                logger.error(Notice('diagnostic.stream.system_default_microphone_not_found', value0=e))
            else:
                logger.error(Notice('diagnostic.stream.selected_microphone_not_found', value0=device_selector, value1=e))
            return None
        
        # 创建音频流
        stream = None
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
                finished_callback=partial(self._on_stream_finished, ready_event=ready_event),
            )
            self.state.stream = stream
            self._running = True
            stream.start()
            if self._shutdown.is_set():
                self._stop_locked(keep_monitor=False)
                return None
            
            self.state.stream = stream
            self._running = True
            self._commit_input_device(device_name)
            logger.debug(
                Notice('diagnostic.stream.audio_stream_started_sample_rate_block_size', value0=self.SAMPLE_RATE, value1=int(self.BLOCK_DURATION * self.SAMPLE_RATE))
            )

            return stream
            
        except Exception as e:
            logger.error(Notice('diagnostic.stream.failed_to_create_audio_stream', value0=e), exc_info=True)
            self._running = False
            self._ready_event = threading.Event()
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    logger.warning(Notice('diagnostic.stream.failed_to_close_partially_started_input_stream'))
                else:
                    self.state.stream = None
            return None
    
    def stop(self, keep_monitor: bool = False) -> None:
        """在线程安全的生命周期锁内停止音频流。"""
        try:
            with self._stream_lock:
                self._stop_locked(keep_monitor=keep_monitor)
        finally:
            if not keep_monitor:
                self._join_monitor()

    def _stop_locked(self, keep_monitor: bool = False) -> None:
        """
        停止音频流
        
        Args:
            keep_monitor: 是否保持监控线程的运行标志。在重载驱动重建流时，应设为 True。
        """
        self._ready_event = threading.Event()
        self._recovery_requested = None
            
        self._running = False  # 标记为停止

        # 仅在需要彻底释放硬件服务时关闭后台监听线程
        if not keep_monitor:
            self._monitor_running = False
            self._monitor_wakeup.set()

        if self.state.stream is not None:
            try:
                self.state.stream.close()
                logger.debug(Notice('diagnostic.stream.audio_stream_stopped'))
            except Exception as e:
                logger.debug(Notice('diagnostic.stream.failed_to_stop_audio_stream', value0=e))
                raise
            else:
                self.state.stream = None

    def _join_monitor(self):
        thread = self._monitor_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
            if not thread.is_alive():
                self._monitor_thread = None
            else:
                logger.warning(Notice('diagnostic.stream.audio_monitor_has_not_exited_before_the_shutdown'))

    def request_shutdown(self):
        """Publish the permanent stop barrier before waiting for backend calls."""
        self._shutdown.set()
        self._monitor_wakeup.set()

    def close(self):
        """Stop the backend and join the one monitor/recovery owner."""
        self.request_shutdown()
        self.stop()
    
    def reopen(self) -> Optional[sd.InputStream]:
        """
        重新启动音频流
        
        Returns:
            新创建的音频输入流
        """
        logger.info(Notice('diagnostic.stream.restarting_audio_stream'))
        
        with self._stream_lock:
            if self._shutdown.is_set() or self.state.dictation_paused:
                return None
            # 停止旧流，但指示监控线程保持运行，防止其被销毁
            self.stop(keep_monitor=True)

            # 重载 PortAudio，更新设备列表
            # 注意：不使用 sd._ffi.dlclose/dlopen 手动卸载/重载 DLL——
            # 这是私有 API，在 Windows 上行为不可靠，且存在与监控线程的竞态，
            # 可导致 access violation 崩溃（进程直接退出，无任何 Python 异常记录）。
            # sd._terminate() + sd._initialize() 足以刷新设备枚举。
            refresh_devices(sd)

            # 等待设备稳定
            if self._shutdown.wait(0.1):
                return None

            # 启动新流
            return self.start()
