# coding: utf-8
"""
WebSocket 管理器 (SocketManager)

负责维护 ASR 服务器的异步通讯层，包括 WebSocket Server 的生命周期管理、
心跳监控、数据发送任务的编排。
"""

import asyncio
import ipaddress
import math
import secrets
import ssl
from http import HTTPStatus
from pathlib import Path

import websockets
from config_server import ServerConfig as Config
from .ws_recv import ws_recv
from .ws_send import ws_send, _retire_connection
from ..delivery import ResultDeliveryError
from .. import logger # Server module logger


MIN_AUTH_TOKEN_LENGTH = 32


def _websockets_major_version() -> int:
    """返回 websockets 主版本号，无法识别时按旧版 API 处理。"""
    try:
        return int(websockets.__version__.split('.', 1)[0])
    except (AttributeError, TypeError, ValueError):
        return 0


def _is_loopback_address(address: str) -> bool:
    """判断监听地址是否严格限制在本机回环接口。"""
    if address.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _has_valid_bearer_token(headers, expected_token: str) -> bool:
    """使用常量时间比较校验 Authorization: Bearer <token>。"""
    try:
        authorization = headers.get('Authorization', '')
    except Exception:
        # 重复或畸形的 Authorization Header 必须按认证失败处理
        return False
    scheme, separator, supplied_token = authorization.partition(' ')
    if not separator or scheme.lower() != 'bearer' or not supplied_token:
        return False
    try:
        return secrets.compare_digest(supplied_token, expected_token)
    except TypeError:
        return False


class SocketManager:
    """
    WebSocket 网络管理器
    
    负责拉起并维护 WebSocket Server 以及识别结果的异步发送任务。
    """
    def __init__(self, app):
        self.app = app
        self._is_running = False
        self._server = None  # websockets.serve 返回的 server 对象
        self._network_mode = 'local'
        self._auth_token = ''
        self._ssl_context = None
        self._prepared = False
        self._active_connections = 0
        self._max_connections = 8
        self._max_message_size = 6 * 1024 * 1024
        self._max_queue = 16
        self._delivery_failed = False

    def prepare(self):
        """在启动托盘、模型进程和监听器前校验安全配置。"""
        network_mode = str(getattr(Config, 'network_mode', 'local')).lower()
        address = str(getattr(Config, 'addr', '127.0.0.1'))
        auth_token = str(getattr(Config, 'auth_token', '')).strip()
        certfile = str(getattr(Config, 'tls_certfile', '')).strip()
        keyfile = str(getattr(Config, 'tls_keyfile', '')).strip()

        integer_limits = {
            'websocket_max_message_bytes': 6 * 1024 * 1024,
            'websocket_max_queue': 16,
            'max_connections': 8,
            'max_message_audio_bytes': 4 * 1024 * 1024,
            'max_task_audio_bytes': 4 * 60 * 60 * 16000 * 4,
            'max_context_length': 4096,
            'max_tasks_per_connection': 4,
            'queue_in_maxsize': 32,
            'queue_out_maxsize': 32,
            'worker_buffer_max_tasks': 64,
        }
        parsed_limits = {}
        for name, default in integer_limits.items():
            try:
                value = int(getattr(Config, name, default))
            except (TypeError, ValueError) as exc:
                raise ValueError(f'ServerConfig.{name} 必须是正整数') from exc
            if value <= 0:
                raise ValueError(f'ServerConfig.{name} 必须是正整数')
            parsed_limits[name] = value

        try:
            idle_timeout = float(getattr(Config, 'connection_idle_timeout', 300))
            task_duration = float(getattr(Config, 'max_task_duration', 6 * 60 * 60))
        except (TypeError, ValueError) as exc:
            raise ValueError('连接空闲和任务时限必须是正数') from exc
        if (
            not math.isfinite(idle_timeout)
            or not math.isfinite(task_duration)
            or idle_timeout <= 0
            or task_duration <= 0
        ):
            raise ValueError('连接空闲和任务时限必须是正数')

        encoded_audio_size = (parsed_limits['max_message_audio_bytes'] + 2) // 3 * 4
        if parsed_limits['websocket_max_message_bytes'] < encoded_audio_size + 65536:
            raise ValueError(
                'websocket_max_message_bytes 太小，必须容纳 Base64 音频消息'
            )

        if network_mode not in {'local', 'lan'}:
            raise ValueError("ServerConfig.network_mode 必须为 'local' 或 'lan'")

        if network_mode == 'local' and not _is_loopback_address(address):
            raise ValueError(
                "local 模式只能监听回环地址；如需局域网访问，请显式设置 "
                "network_mode = 'lan' 并配置 CAPSWRITER_AUTH_TOKEN"
            )

        if network_mode == 'lan' and len(auth_token) < MIN_AUTH_TOKEN_LENGTH:
            raise ValueError(
                f"lan 模式要求 CAPSWRITER_AUTH_TOKEN 至少 {MIN_AUTH_TOKEN_LENGTH} 个字符"
            )

        if bool(certfile) != bool(keyfile):
            raise ValueError('tls_certfile 与 tls_keyfile 必须同时配置')

        ssl_context = None
        if certfile and keyfile:
            if not Path(certfile).is_file() or not Path(keyfile).is_file():
                raise ValueError('TLS 证书或私钥文件不存在')
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            try:
                ssl_context.load_cert_chain(certfile, keyfile)
            except (OSError, ssl.SSLError) as exc:
                raise ValueError(f'TLS 证书或私钥加载失败: {exc}') from exc

        self._network_mode = network_mode
        self._auth_token = auth_token
        self._ssl_context = ssl_context
        self._max_connections = parsed_limits['max_connections']
        self._max_message_size = parsed_limits['websocket_max_message_bytes']
        self._max_queue = parsed_limits['websocket_max_queue']
        self._prepared = True

    async def _handle_connection(self, websocket):
        """拒绝超过并发上限的连接，不让其在服务器中排队等待。"""
        if self._delivery_failed:
            from .ws_send import _close_failed_connection
            await _close_failed_connection(websocket, 'Recognition channel unavailable')
            return
        if self._active_connections >= self._max_connections:
            logger.warning('拒绝超出并发上限的 WebSocket 连接: %s', websocket.remote_address)
            await websocket.close(code=1013, reason='服务器连接数已达上限')
            return

        self._active_connections += 1
        try:
            await ws_recv(websocket, self.app)
        finally:
            self._active_connections -= 1

    def _build_auth_process_request(self):
        """按 websockets 版本生成握手阶段的令牌认证回调。"""
        if self._network_mode != 'lan':
            return None

        if _websockets_major_version() >= 14:
            def process_request(connection, request):
                if _has_valid_bearer_token(request.headers, self._auth_token):
                    return None
                logger.warning('拒绝未经认证的 WebSocket 连接: %s', connection.remote_address)
                return connection.respond(HTTPStatus.UNAUTHORIZED, 'Unauthorized\n')

            return process_request

        async def legacy_process_request(path, request_headers):
            if _has_valid_bearer_token(request_headers, self._auth_token):
                return None
            logger.warning('拒绝未经认证的 WebSocket 连接')
            body = b'Unauthorized\n'
            return (
                HTTPStatus.UNAUTHORIZED,
                [('Content-Type', 'text/plain'), ('Content-Length', str(len(body)))],
                body,
            )

        return legacy_process_request

    def _check_port(self):
        """检查端口可用性"""
        import socket
        family = socket.AF_INET6 if ':' in str(Config.addr) else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as s:
            try:
                s.bind((Config.addr, int(Config.port)))
                return True
            except socket.error:
                logger.error(f"端口冲突：{Config.addr}:{Config.port} 已被占用，请检查是否已有服务端正在运行。")
                return False

    async def start(self):
        """
        启动 WebSocket 网络服务
        """
        if self._is_running: return

        if not self._prepared:
            self.prepare()
        
        # 0. 启动前自检环境
        if not self._check_port():
            return

        self._is_running = True

        loop = self.app.loop
        
        # 1. 优化守护线程执行器 (防止阻塞事件循环)
        from core.tools.daemon_executor import SimpleDaemonExecutor
        loop.set_default_executor(SimpleDaemonExecutor())

        # 3. 启动服务
        scheme = 'wss' if self._ssl_context else 'ws'
        logger.info(
            f"正在拉起 WebSocket 服务 (模式: {self._network_mode}, "
            f"监听: {scheme}://{Config.addr}:{Config.port})"
        )
        if self._network_mode == 'lan' and not self._ssl_context:
            logger.warning('LAN 模式当前未启用 TLS，仅应在可信局域网内使用')
        
        async with websockets.serve(
            self._handle_connection,
            Config.addr,
            Config.port,
            subprotocols=["binary"],
            origins=[None],
            process_request=self._build_auth_process_request(),
            ssl=self._ssl_context,
            max_size=self._max_message_size,
            max_queue=self._max_queue,
            close_timeout=5.0,
        ) as server:
            self._server = server  # 保存 server 引用，用于外部关闭

            # 4. 进入识别结果发送循环 (作为主阻塞任务)
            logger.info("WebSocket 发送协程已就绪")
            try:
                await ws_send(self.app)
            except ResultDeliveryError as exc:
                self._delivery_failed = True
                logger.critical('Recognition channel unavailable; restart required: %s', str(exc))
                server.close()
                await asyncio.gather(*(
                    _retire_connection(self.app.state, socket, 'Recognition channel unavailable')
                    for socket in list(self.app.state.sockets.values())
                ), return_exceptions=True)
            
        self._is_running = False
        self._server = None
        logger.info("SocketManager: WebSocket 服务已退出")

    def stop(self):
        """停止 WebSocket 网络服务"""
        # 主动关闭 WebSocket 服务器，让 ws_send 的 await 尽快返回
        if self._server:
            self._server.close()
        self._is_running = False
