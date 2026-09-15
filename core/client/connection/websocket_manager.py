# coding: utf-8
"""
WebSocket 连接管理模块

提供 WebSocketManager 类用于管理与服务端的 WebSocket 连接，
包括连接建立、重连、消息发送和连接状态检查。
"""

from __future__ import annotations

import json
import ssl
from typing import TYPE_CHECKING, Optional

import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from config_client import ClientConfig as Config
from core.protocol import AudioMessage, RecognitionMessage
from ..state import console
from .. import logger
import asyncio


if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import CapsWriterClient


def _websockets_major_version() -> int:
    """返回 websockets 主版本号，无法识别时按旧版 API 处理。"""
    try:
        return int(websockets.__version__.split('.', 1)[0])
    except (AttributeError, TypeError, ValueError):
        return 0


class CommunicationError(Exception):
    """通信层通用异常"""
    pass


class WebSocketManager:
    """
    WebSocket 连接管理器

    负责管理与识别服务端的 WebSocket 连接，提供自动重连和
    错误处理功能。

    Attributes:
        app: 客户端 App 实例
        max_retries: 最大重试次数
    """

    def __init__(self, app: CapsWriterClient):
        """
        初始化 WebSocket 管理器

        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._connect_fail_logged = False  # 断联后只记一次失败日志
        self._shutdown_requested = False

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state
    
    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self.state.is_connected
    
    async def connect(self, *, announce: bool = True) -> bool:
        """
        建立 WebSocket 连接

        尝试连接到配置的服务端地址，如果失败会自动重试。

        Returns:
            连接是否成功
        """
        # 退出流程一旦开始就不能再重连，否则事件循环停止时可能中断
        # 刚建立的 TCP 连接，在服务端留下不完整的 WebSocket 握手。
        if self._shutdown_requested:
            return False

        # 如果已连接，直接返回
        if self.is_connected:
            return True

        # 清理旧连接
        if self.state.websocket is not None:
            self.state.websocket = None

        use_tls = bool(getattr(Config, 'use_tls', False))
        scheme = 'wss' if use_tls else 'ws'
        url = f"{scheme}://{Config.addr}:{Config.port}"

        try:
            if not self._connect_fail_logged:
                logger.debug(f"正在连接服务端 {url}")

            kwargs = dict(
                uri=url,
                subprotocols=["binary"],
                max_size=int(getattr(Config, 'websocket_max_message_bytes', 16 * 1024 * 1024)),
                max_queue=int(getattr(Config, 'websocket_max_queue', 16)),
            )

            auth_token = str(getattr(Config, 'auth_token', '')).strip()
            if auth_token:
                header_name = (
                    'additional_headers'
                    if _websockets_major_version() >= 14
                    else 'extra_headers'
                )
                kwargs[header_name] = {'Authorization': f'Bearer {auth_token}'}

            if use_tls:
                ca_file = str(getattr(Config, 'tls_ca_file', '')).strip() or None
                kwargs['ssl'] = ssl.create_default_context(cafile=ca_file)

            # websockets>=16.0 默认走代理，本地连接需显式禁用，但 14 才引入这个参数
            if _websockets_major_version() >= 14:
                kwargs["proxy"] = None  
            
            websocket = await websockets.connect(**kwargs)

            # connect() 期间也可能收到退出请求。此时完成关闭握手，但不再
            # 把连接发布到共享状态，避免结果处理循环继续使用它。
            if self._shutdown_requested:
                await websocket.close()
                return False

            self.state.websocket = websocket

            if announce:
                console.print(
                    f'[ui.success]●[/] [ui.label]服务端在线[/]  '
                    f'[ui.value]{Config.addr}:{Config.port}[/]'
                )
            logger.info(f"WebSocket 建立成功: {url}")
            self._connect_fail_logged = False
            return True

        except (ConnectionRefusedError, TimeoutError):
            if not self._connect_fail_logged:
                logger.debug(f"连接服务端 {url} 被拒绝或超时")
                self._connect_fail_logged = True
        except Exception as e:
            if not self._connect_fail_logged:
                logger.debug(f"连接服务端 {url} 失败: {e}")
                self._connect_fail_logged = True
        
        return False
    
    async def send(self, message: AudioMessage) -> bool:
        """
        发送消息到服务端
        
        Args:
            message: 要发送的 AudioMessage 对象
            
        Returns:
            发送是否成功
        """
        if not self.is_connected:
            logger.warning("无法发送消息：WebSocket 未连接")
            return False
        
        try:
            await self.state.websocket.send(message.to_json())
            return True
            
        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as exc:
            self.state.websocket = None
            detail = exc.reason or f'关闭代码 {exc.code}'
            raise CommunicationError(f"发送失败：服务端已关闭连接（{detail}）")
            
        except Exception as e:
            raise CommunicationError(f"发送消息时发生未知错误: {e}")
    
    async def receive(self) -> Optional[RecognitionMessage]:
        """
        接收服务端消息
        
        Returns:
            解析后的 RecognitionMessage；退出或未连接时返回 None。

        Raises:
            CommunicationError: 非退出状态下的断线或消息解析失败。
        """
        if self._shutdown_requested:
            return None
        # recv() 等待期间 stop/reset 可能清空共享引用，保留本次接收的连接。
        websocket = self.state.websocket
        if websocket is None or not self.is_connected:
            logger.warning("无法接收消息：WebSocket 未连接")
            return None
        
        try:
            raw_message = await websocket.recv()
            data = json.loads(raw_message)
            return RecognitionMessage.from_dict(data)
            
        except (ConnectionClosedError, ConnectionClosedOK) as exc:
            if self.state.websocket is websocket:
                self.state.websocket = None
            if self._shutdown_requested:
                return None
            close_frame = exc.rcvd or exc.sent
            detail = (close_frame.reason or f'关闭代码 {close_frame.code}') if close_frame else '连接异常中断'
            raise CommunicationError(f"接收失败：服务端已关闭连接（{detail}）") from exc
            
        except json.JSONDecodeError as e:
            raise CommunicationError(f"消息解析失败: {e}")
            
        except Exception as e:
            raise CommunicationError(f"接收消息时发生未知错误: {e}")
    
    async def close(self) -> None:
        """关闭 WebSocket 连接"""
        websocket = self.state.websocket
        if websocket is not None:
            await websocket.close()
            if self.state.websocket is websocket:
                self.state.websocket = None
            logger.info("WebSocket 连接已关闭")

    def begin_shutdown(self) -> None:
        """同步发布退出状态，阻止新的连接和自动重连。"""
        self._shutdown_requested = True

    def close_sync(self) -> None:
        """
        从同步上下文（如 teardown）关闭连接
        
        使用 run_coroutine_threadsafe 安全地将关闭操作调度到已有的事件循环。
        如果事件循环未运行，则直接置空连接引用。
        """
        # 必须在调度异步 close 之前同步设置；退出可能来自托盘线程，
        # 结果处理循环此时仍在事件循环线程中运行。
        self.begin_shutdown()

        websocket = self.state.websocket
        if websocket is None:
            return

        loop = self.app.loop
        if loop and loop.is_running():
            # 捕获当前连接，避免随后 State.reset() 先清空共享引用，导致实际
            # close 协程执行时找不到需要关闭的连接。
            asyncio.run_coroutine_threadsafe(websocket.close(), loop)
            logger.debug("已调度 WebSocket 关闭（threadsafe）")
        else:
            # 事件循环已停止，直接清空引用
            self.state.websocket = None
            logger.debug("事件循环已停，直接置空 WebSocket 引用")
