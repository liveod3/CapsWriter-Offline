# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import sys
import time
import threading
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

from core.client.state import console
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
        self._channels = 1
        self._running = False  # 标志是否应该运行
        self._last_default_device = None
        self._monitor_thread = None
        self._monitor_running = False

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state
    
    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags
    ) -> None:
        """
        音频数据回调函数
        
        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
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
        self.reopen()

    def _device_monitor_loop(self) -> None:
        """后台静默监控系统默认输入设备变化的循环"""
        while self._monitor_running:
            time.sleep(4.0)  # 每 4 秒检测一次硬件状态
            
            # 如果用户当前正在录音说话，绝对不要打断当前的音频流
            if self.state.recording:
                continue
                
            try:
                # 1. 临时停止当前的流，以便释放 PortAudio 锁进行重载
                self.stop(keep_monitor=True)
                
                # 2. 彻底重载 PortAudio 驱动，迫使底层重新扫描物理插槽以绕过设备缓存限制
                try:
                    sd._terminate()
                    sd._ffi.dlclose(sd._lib)
                    sd._lib = sd._ffi.dlopen(sd._libname)
                    sd._initialize()
                except Exception as e:
                    logger.debug(f"设备监控重载 PortAudio 失败: {e}")
                    # 容错恢复：静默重启旧流
                    self.start(silent=True)
                    continue
                
                # 3. 重新获取系统当前的默认输入设备
                device = sd.query_devices(kind='input')
                current_device_name = device.get('name')
                
                if current_device_name and self._last_default_device and current_device_name != self._last_default_device:
                    # 侦测到系统默认设备发生了真实物理改变（例如插回了蓝牙/有线耳机）
                    logger.info(f"监控线程检测到系统默认音频设备变更: {self._last_default_device} -> {current_device_name}")
                    console.print(
                        f'\n[yellow]检测到默认音频设备变更，已自动切回：{current_device_name}[/yellow]',
                        end='\n\n'
                    )
                    self._last_default_device = current_device_name
                    # 重新拉起音频流（非静默模式，打印设备通道信息）
                    self.start(silent=False)
                else:
                    # 设备无变动，静默重启音频流，保障后台监听无缝续接
                    self.start(silent=True)
                    
            except Exception as e:
                logger.debug(f"后台硬件监听循环异常: {e}")
                # 确保在任何意外错误后，底层的录音流一定能够被拉起
                if not self._running:
                    self.start(silent=True)
    
    def start(self, silent: bool = False) -> Optional[sd.InputStream]:
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
            
        # 检测音频设备
        try:
            device = sd.query_devices(kind='input')
            self._channels = min(2, device['max_input_channels'])
            device_name = device.get('name', '未知设备')
            self._last_default_device = device_name
            
            if not silent:
                console.print(
                    f'使用默认音频设备：[italic]{device_name}，声道数：{self._channels}',
                    end='\n\n'
                )
            logger.info(f"找到音频设备: {device_name}, 声道数: {self._channels}")
        except UnicodeDecodeError:
            logger.warning("无法获取音频设备名称（编码问题）")
        except sd.PortAudioError:
            logger.error("未找到麦克风设备")
            input('按回车键退出')
            sys.exit(1)
        
        # 创建音频流
        try:
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                device=None,
                dtype="float32",
                channels=self._channels,
                callback=self._audio_callback,
                finished_callback=self._on_stream_finished,
            )
            stream.start()
            
            self.state.stream = stream
            self._running = True
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
        """
        停止音频流
        
        Args:
            keep_monitor: 是否保持监控线程的运行标志。在重载驱动重建流时，应设为 True。
        """
        if not self._running:
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
        
        # 停止旧流，但指示监控线程保持运行，防止其被销毁
        self.stop(keep_monitor=True)
        
        # 重载 PortAudio，更新设备列表
        try:
            sd._terminate()
            sd._ffi.dlclose(sd._lib)
            sd._lib = sd._ffi.dlopen(sd._libname)
            sd._initialize()
        except Exception as e:
            logger.warning(f"重载 PortAudio 时发生警告: {e}")
        
        # 等待设备稳定
        time.sleep(0.1)
        
        # 启动新流
        return self.start()