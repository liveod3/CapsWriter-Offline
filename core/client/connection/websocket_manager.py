# coding: utf-8
"""
WebSocket connection management.

Use WebSocketManager to connect to the server,
reconnect, send messages, and check connection state.
"""

from __future__ import annotations

from core.i18n import Notice, tr

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
    """Return the websockets major version, defaulting to the legacy API if unknown."""
    try:
        return int(websockets.__version__.split('.', 1)[0])
    except (AttributeError, TypeError, ValueError):
        return 0


class CommunicationError(Exception):
    """Base transport exception."""
    pass


class WebSocketManager:
    """
    Manage a WebSocket connection.

    Connect to the recognition server with automatic reconnection
    and error handling.

    Attributes:
        app: Client App instance.
        max_retries: Maximum connection retries.
    """

    def __init__(self, app: CapsWriterClient):
        """
        Initialize the WebSocket manager.

        Args:
            app: Client App instance.
        """
        self.app = app
        self._connect_fail_logged = False  # Report a connection failure only once per disconnect.
        self._shutdown_requested = False

    @property
    def state(self) -> ClientState:
        """Access shared client state."""
        return self.app.state
    
    @property
    def is_connected(self) -> bool:
        """Return whether the connection is open."""
        return self.state.is_connected
    
    async def connect(self, *, announce: bool = True) -> bool:
        """
        Connect to the server.

        Retry failed connections to the configured address.

        Returns:
            Whether connection succeeded.
        """
        # Do not reconnect after shutdown starts: stopping the loop could interrupt
        # a new TCP connection and leave an incomplete WebSocket handshake.
        if self._shutdown_requested:
            return False

        # Return immediately if already connected.
        if self.is_connected:
            return True

        # Close the previous connection.
        if self.state.websocket is not None:
            self.state.websocket = None

        use_tls = bool(getattr(Config, 'use_tls', False))
        scheme = 'wss' if use_tls else 'ws'
        url = f"{scheme}://{Config.addr}:{Config.port}"

        try:
            if not self._connect_fail_logged:
                logger.debug(Notice('diagnostic.websocket_manager.connecting_to_server', value0=url))

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

            # websockets 16 defaults to proxies; disable them for local connections on versions >=14.
            if _websockets_major_version() >= 14:
                kwargs["proxy"] = None  
            
            websocket = await websockets.connect(**kwargs)

            # Shutdown can start during connect(). Finish the close handshake without
            # publishing the connection for result consumers to reuse.
            if self._shutdown_requested:
                await websocket.close()
                return False

            self.state.websocket = websocket

            if announce:
                console.print(
                    tr('connection.online', value0=Config.addr, value1=Config.port)
                )
            logger.info(Notice('diagnostic.websocket_manager.websocket_connected', value0=url))
            self._connect_fail_logged = False
            return True

        except (ConnectionRefusedError, TimeoutError):
            if not self._connect_fail_logged:
                logger.debug(Notice('diagnostic.websocket_manager.connection_to_server_refused_or_timed_out', value0=url))
                self._connect_fail_logged = True
        except Exception as e:
            if not self._connect_fail_logged:
                logger.debug(Notice('diagnostic.websocket_manager.websocket_connection_failed_error'), type(e).__name__)
                self._connect_fail_logged = True
        
        return False
    
    async def send(self, message: AudioMessage) -> bool:
        """
        Send a server message.
        
        Args:
            message: AudioMessage to send.
            
        Returns:
            Whether sending succeeded.
        """
        if not self.is_connected:
            logger.warning(Notice('diagnostic.websocket_manager.cannot_send_message_websocket_is_disconnected'))
            return False
        
        try:
            await self.state.websocket.send(message.to_json())
            return True
            
        except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as exc:
            self.state.websocket = None
            close_frame = exc.rcvd or exc.sent
            code = close_frame.code if close_frame else 1006
            raise CommunicationError(Notice('validation.websocket_manager.send_failed_connection_closed_code', value0=code)) from None
            
        except Exception as e:
            raise CommunicationError(Notice('validation.websocket_manager.send_failed', value0=type(e).__name__)) from None
    
    async def receive(self) -> Optional[RecognitionMessage]:
        """
        Receive server messages.
        
        Returns:
            Parsed RecognitionMessage, or None while disconnected or stopping.

        Raises:
            CommunicationError: Unexpected disconnect or invalid response outside shutdown.
        """
        if self._shutdown_requested:
            return None
        # Keep the receiving connection locally; stop/reset may clear the shared reference.
        websocket = self.state.websocket
        if websocket is None or not self.is_connected:
            logger.warning(Notice('diagnostic.websocket_manager.cannot_receive_message_websocket_is_disconnected'))
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
            code = close_frame.code if close_frame else 1006
            raise CommunicationError(Notice('validation.websocket_manager.receive_failed_connection_closed_code', value0=code)) from None
            
        except json.JSONDecodeError:
            raise CommunicationError(Notice('validation.websocket_manager.invalid_message_json')) from None
            
        except Exception as e:
            raise CommunicationError(Notice('validation.websocket_manager.receive_failed', value0=type(e).__name__)) from None
    
    async def close(self) -> None:
        """Close the WebSocket connection."""
        websocket = self.state.websocket
        if websocket is not None:
            await websocket.close()
            if self.state.websocket is websocket:
                self.state.websocket = None
            logger.info(Notice('diagnostic.websocket_manager.websocket_connection_closed'))

    def begin_shutdown(self) -> None:
        """Publish shutdown synchronously to prevent new connections and retries."""
        self._shutdown_requested = True

    def close_sync(self) -> None:
        """
        Close the connection from synchronous code such as teardown.
        
        Schedule closure on the existing loop with run_coroutine_threadsafe.
        Clear the connection reference directly if the loop is not running.
        """
        # Set shutdown before scheduling close: the tray thread can initiate exit
        # while the result consumer still runs on the event-loop thread.
        self.begin_shutdown()

        websocket = self.state.websocket
        if websocket is None:
            return

        loop = self.app.loop
        if loop and loop.is_running():
            # Capture the connection before State.reset() clears the shared reference,
            # so the close coroutine can still reach it.
            asyncio.run_coroutine_threadsafe(websocket.close(), loop)
            logger.debug(Notice('diagnostic.websocket_manager.websocket_closure_scheduled_on_its_event_loop'))
        else:
            # The loop has stopped; clear the reference directly.
            self.state.websocket = None
            logger.debug(Notice('diagnostic.websocket_manager.event_loop_stopped_websocket_reference_cleared'))
