"""Bound microphone transport cleanup without replacing a newer connection."""

import asyncio

from core.client.transcribe.lifecycle import positive_timeout
from core.client import logger
from core.protocol import CancelMessage


MAX_PENDING_DICTATIONS = 64
CLOSE_TIMEOUT = 5.0


class DictationSendError(RuntimeError):
    """Audio upload failed before a usable final result could be accepted."""


async def cancel_dictation(state, websocket, task_id):
    """Best-effort scoped cancellation; failed delivery falls back to disconnect."""
    if websocket is None or state.websocket is not websocket:
        return
    try:
        await asyncio.wait_for(websocket.send(CancelMessage(task_id).to_json()), CLOSE_TIMEOUT)
    except asyncio.CancelledError:
        transport = getattr(websocket, 'transport', None)
        if transport is not None:
            transport.abort()
        raise
    except Exception:
        await close_dictation_connection(state, websocket)


async def close_dictation_connection(state, websocket):
    if websocket is None:
        return
    try:
        await asyncio.wait_for(websocket.close(), CLOSE_TIMEOUT)
    except asyncio.CancelledError:
        transport = getattr(websocket, 'transport', None)
        if transport is not None:
            transport.abort()
        raise
    except Exception as exc:
        logger.warning('Dictation connection cleanup failed: %s', type(exc).__name__)
        transport = getattr(websocket, 'transport', None)
        if transport is not None:
            transport.abort()
    finally:
        if state.websocket is websocket:
            state.websocket = None


def dictation_timeouts(config):
    return (positive_timeout(config, 'mic_io_timeout', 60.0),
            positive_timeout(config, 'mic_result_timeout', 600.0))
