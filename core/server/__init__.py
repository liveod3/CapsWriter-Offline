# coding: utf-8
"""
Server package.

Provide CapsWriter server components.

Package layout:
- state: Shared connections and queues.
- schema: Task and Result dataclasses.
- worker/check_model: Model file validation.
- worker/worker: Recognition process entry point.
- worker/pipeline: Recognition pipeline.
- connection/ws_recv: WebSocket input.
- connection/ws_send: WebSocket output.
"""

from core.server.state import console
from core.logger import get_logger, setup_logger
from config_server import ServerConfig as Config, __version__

setup_logger('server', level=Config.log_level)
logger = get_logger('server')

from core.server.schema import Task, Result

__all__ = [
    'console',
    'logger',
    'Task',
    'Result',
]
