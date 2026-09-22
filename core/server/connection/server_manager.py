# coding: utf-8
"""
WebSocket manager (SocketManager).

Own asynchronous server communication, including server lifecycle,
liveness monitoring, and result delivery tasks.
"""

from core.i18n import Notice

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
    """Return the websockets major version, defaulting to the legacy API if unknown."""
    try:
        return int(websockets.__version__.split('.', 1)[0])
    except (AttributeError, TypeError, ValueError):
        return 0


def _is_loopback_address(address: str) -> bool:
    """Return whether the bind address is restricted to loopback."""
    if address.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _has_valid_bearer_token(headers, expected_token: str) -> bool:
    """Validate Authorization: Bearer <token> with constant-time comparison."""
    try:
        authorization = headers.get('Authorization', '')
    except Exception:
        # Reject duplicate or malformed Authorization headers.
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
    WebSocket network manager.
    
    Start and maintain the server and asynchronous result delivery.
    """
    def __init__(self, app):
        self.app = app
        self._is_running = False
        self._server = None  # Server object returned by websockets.serve.
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
        """Validate security before starting tray, model processes, or listeners."""
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
                raise ValueError(Notice('validation.server_manager.serverconfig_must_be_a_positive_integer', value0=name)) from exc
            if value <= 0:
                raise ValueError(Notice('validation.server_manager.serverconfig_must_be_a_positive_integer', value0=name))
            parsed_limits[name] = value

        try:
            idle_timeout = float(getattr(Config, 'connection_idle_timeout', 300))
            task_duration = float(getattr(Config, 'max_task_duration', 6 * 60 * 60))
        except (TypeError, ValueError) as exc:
            raise ValueError(Notice('validation.server_manager.connection_idle_and_task_timeouts_must_be_positive')) from exc
        if (
            not math.isfinite(idle_timeout)
            or not math.isfinite(task_duration)
            or idle_timeout <= 0
            or task_duration <= 0
        ):
            raise ValueError(Notice('validation.server_manager.connection_idle_and_task_timeouts_must_be_positive'))

        encoded_audio_size = (parsed_limits['max_message_audio_bytes'] + 2) // 3 * 4
        if parsed_limits['websocket_max_message_bytes'] < encoded_audio_size + 65536:
            raise ValueError(
                Notice('validation.server_manager.websocket_max_message_bytes_must_be_large_enough')
            )

        if network_mode not in {'local', 'lan'}:
            raise ValueError(Notice('validation.server_manager.serverconfig_network_mode_must_be_local_or_lan'))

        if network_mode == 'local' and not _is_loopback_address(address):
            raise ValueError(
                Notice('validation.server_manager.local_mode_requires_a_loopback_address_for_lan')
            )

        if network_mode == 'lan' and len(auth_token) < MIN_AUTH_TOKEN_LENGTH:
            raise ValueError(
                Notice('validation.server_manager.lan_mode_requires_capswriter_auth_token_with_at', value0=MIN_AUTH_TOKEN_LENGTH)
            )

        if bool(certfile) != bool(keyfile):
            raise ValueError(Notice('validation.server_manager.tls_certfile_and_tls_keyfile_must_both_be'))

        ssl_context = None
        if certfile and keyfile:
            if not Path(certfile).is_file() or not Path(keyfile).is_file():
                raise ValueError(Notice('validation.server_manager.tls_certificate_or_private_key_file_does_not'))
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            try:
                ssl_context.load_cert_chain(certfile, keyfile)
            except (OSError, ssl.SSLError) as exc:
                raise ValueError(Notice('validation.server_manager.failed_to_load_tls_certificate_or_private_key', value0=exc)) from exc

        self._network_mode = network_mode
        self._auth_token = auth_token
        self._ssl_context = ssl_context
        self._max_connections = parsed_limits['max_connections']
        self._max_message_size = parsed_limits['websocket_max_message_bytes']
        self._max_queue = parsed_limits['websocket_max_queue']
        self._prepared = True

    async def _handle_connection(self, websocket):
        """Reject excess connections instead of queuing them on the server."""
        if self._delivery_failed:
            from .ws_send import _close_failed_connection
            await _close_failed_connection(websocket, 'Recognition channel unavailable')
            return
        if self._active_connections >= self._max_connections:
            logger.warning(Notice('diagnostic.server_manager.websocket_connection_rejected_concurrent_connection_limit_reached'), websocket.remote_address)
            await websocket.close(code=1013, reason=str(Notice('server.connection_limit')))
            return

        self._active_connections += 1
        try:
            await ws_recv(websocket, self.app)
        finally:
            self._active_connections -= 1

    def _build_auth_process_request(self):
        """Build a handshake authentication callback for the installed websockets API."""
        if self._network_mode != 'lan':
            return None

        if _websockets_major_version() >= 14:
            def process_request(connection, request):
                if _has_valid_bearer_token(request.headers, self._auth_token):
                    return None
                logger.warning(Notice('diagnostic.server_manager.unauthenticated_websocket_connection_rejected_details'), connection.remote_address)
                return connection.respond(HTTPStatus.UNAUTHORIZED, 'Unauthorized\n')

            return process_request

        async def legacy_process_request(path, request_headers):
            if _has_valid_bearer_token(request_headers, self._auth_token):
                return None
            logger.warning(Notice('diagnostic.server_manager.unauthenticated_websocket_connection_rejected'))
            body = b'Unauthorized\n'
            return (
                HTTPStatus.UNAUTHORIZED,
                [('Content-Type', 'text/plain'), ('Content-Length', str(len(body)))],
                body,
            )

        return legacy_process_request

    def _check_port(self):
        """Check port availability."""
        import socket
        family = socket.AF_INET6 if ':' in str(Config.addr) else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as s:
            try:
                s.bind((Config.addr, int(Config.port)))
                return True
            except socket.error:
                logger.error(Notice('diagnostic.server_manager.port_conflict_is_already_in_use_check_whether', value0=Config.addr, value1=Config.port))
                return False

    async def start(self):
        """
        Start the WebSocket server.
        """
        if self._is_running: return

        if not self._prepared:
            self.prepare()
        
        # 0. Validate prerequisites.
        if not self._check_port():
            return

        self._is_running = True

        loop = self.app.loop
        
        # 1. Configure a daemon executor to keep blocking calls off the event loop.
        from core.tools.daemon_executor import SimpleDaemonExecutor
        loop.set_default_executor(SimpleDaemonExecutor())

        # 3. Start the server.
        scheme = 'wss' if self._ssl_context else 'ws'
        logger.info(
            Notice('diagnostic.server_manager.starting_websocket_service_mode_listening', value0=self._network_mode, value1=scheme, value2=Config.addr, value3=Config.port)
        )
        if self._network_mode == 'lan' and not self._ssl_context:
            logger.warning(Notice('diagnostic.server_manager.tls_is_disabled_in_lan_mode_use_only'))
        
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
            self._server = server  # Keep the server reference for external shutdown.

            # 4. Run result delivery as the main awaited task.
            logger.info(Notice('diagnostic.server_manager.websocket_sender_ready'))
            try:
                await ws_send(self.app)
            except ResultDeliveryError as exc:
                self._delivery_failed = True
                logger.critical(Notice('diagnostic.server_manager.recognition_channel_unavailable_restart_required'), str(exc))
                server.close()
                await asyncio.gather(*(
                    _retire_connection(self.app.state, socket, 'Recognition channel unavailable')
                    for socket in list(self.app.state.sockets.values())
                ), return_exceptions=True)
            
        self._is_running = False
        self._server = None
        logger.info(Notice('diagnostic.server_manager.socketmanager_websocket_service_exited'))

    def stop(self):
        """Stop the WebSocket server."""
        # Close the server to unblock result delivery promptly.
        if self._server:
            self._server.close()
        self._is_running = False
