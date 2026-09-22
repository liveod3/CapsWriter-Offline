"""Serialize recording file I/O off the event loop, with bounded FFmpeg recovery."""

from core.i18n import Notice

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from . import logger


class AsyncAudioWriter:
    IO_TIMEOUT = 10.0
    CLEANUP_TIMEOUT = 3.0

    def __init__(self, manager):
        self.manager = manager
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='audio-file')
        self._closed = False
        self._close_task = None

    async def call(self, method, *args):
        if self._closed:
            raise RuntimeError('AudioWriterClosed')
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, partial(method, *args))
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.IO_TIMEOUT)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            # abort() never closes the pipe or waits for the serial file lock.
            await asyncio.to_thread(self.manager.abort)
            try:
                await asyncio.wait_for(asyncio.shield(future), self.CLEANUP_TIMEOUT)
            except Exception as exc:
                logger.warning(Notice('diagnostic.file_writer.interrupted_audio_i_o'), type(exc).__name__)
                # Consume a late exception if the OS operation is still pending.
                future.add_done_callback(self._consume_exception)
            raise

    @staticmethod
    def _consume_exception(future):
        if not future.cancelled():
            future.exception()

    async def close(self, *, abort=False):
        """One cleanup task survives repeated cancellation of its caller."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(abort))
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._close_task)
                break
            except asyncio.CancelledError:
                if self._close_task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self, abort):
        try:
            if abort:
                await asyncio.to_thread(self.manager.abort)
            await self.call(self.manager.finish)
        finally:
            self._closed = True
            self._executor.shutdown(wait=False, cancel_futures=True)
