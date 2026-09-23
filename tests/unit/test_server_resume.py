"""Synthetic scheduling gaps; no actual suspend, IPC, models or devices."""

import asyncio
import importlib
import queue
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.server.delivery import ResultDeliveryError


@pytest.mark.parametrize('outcome', ['result', 'empty', 'broken', 'dead', 'failed',
                                     'stop', 'cancel', 'stalled', 'repeated_gap', 'ready'])
def test_pending_read_after_scheduling_gap(monkeypatch, outcome):
    sender = importlib.import_module('core.server.connection.ws_send')

    async def run():
        clock = SimpleNamespace(now=0.0)
        pending = asyncio.get_running_loop().create_future()
        current = SimpleNamespace(queue_out=SimpleNamespace(get=object()),
                                  worker_failed=threading.Event(),
                                  recognize_process=SimpleNamespace(is_alive=lambda: True))
        app = SimpleNamespace(state=current, is_alive=True)
        item = object()
        polls = 0

        async def read(*args, **kwargs):
            return await pending

        reader = AsyncMock(side_effect=read)

        async def wait(operations, timeout):
            nonlocal polls
            polls += 1
            assert polls <= 3, 'A stalled read must remain bounded'
            await asyncio.sleep(0)
            clock.now += 5600 if polls == 1 or outcome == 'repeated_gap' else 5
            if polls == 1:
                if outcome == 'dead':
                    current.recognize_process.is_alive = lambda: False
                elif outcome == 'failed':
                    current.worker_failed.set()
                elif outcome == 'stop':
                    app.is_alive = False
                elif outcome == 'cancel':
                    raise asyncio.CancelledError
            if polls == 2 or outcome == 'ready':
                if outcome in ('result', 'ready'):
                    pending.set_result(item)
                elif outcome in ('empty', 'broken'):
                    pending.set_exception(queue.Empty() if outcome == 'empty' else OSError())
                await asyncio.sleep(0)
            # The wait result can be stale by the time its caller resumes.
            return set(), operations

        monkeypatch.setattr(sender, 'time', SimpleNamespace(monotonic=lambda: clock.now))
        monkeypatch.setattr(sender, 'to_thread', reader)
        monkeypatch.setattr(sender, 'asyncio', SimpleNamespace(
            create_task=asyncio.create_task, wait=wait, gather=asyncio.gather))
        expected = {'empty': queue.Empty, 'broken': OSError, 'dead': ResultDeliveryError,
                    'failed': ResultDeliveryError, 'stalled': ResultDeliveryError,
                    'repeated_gap': ResultDeliveryError, 'cancel': asyncio.CancelledError}
        if outcome in expected:
            with pytest.raises(expected[outcome]) as caught:
                await sender._next_result(app, 60)
            if outcome in ('stalled', 'repeated_gap'):
                assert str(caught.value) == 'ResultQueueReadTimeout'
        else:
            assert await sender._next_result(app, 60) is (None if outcome == 'stop' else item)
        reader.assert_awaited_once_with(current.queue_out.get, timeout=sender.RESULT_QUEUE_POLL)
        assert polls == (2 if outcome in ('result', 'empty', 'broken', 'stalled', 'repeated_gap') else 1)
        assert pending.done()

    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['recover', 'stalled', 'repeated_gap', 'dead',
                                     'failed', 'aligner_dead', 'second_sleep'])
def test_worker_monitor_after_scheduling_gap(monkeypatch, outcome):
    module = importlib.import_module('core.server.worker.process_manager')
    clock = SimpleNamespace(now=0.0, heartbeat=0.0)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(module, 'progress', lambda _: clock.heartbeat)
    monkeypatch.setattr(module.Config, 'worker_stall_timeout', 600)
    current = SimpleNamespace(worker_failed=threading.Event(), worker_progress=object())
    manager = module.ProcessManager(SimpleNamespace(state=current))
    manager._process = SimpleNamespace(is_alive=lambda: True)
    manager._start_aligner_process = Mock()
    manager._check_runtime()
    clock.now = 5600
    if outcome == 'dead':
        manager._process.is_alive = lambda: False
    elif outcome == 'failed':
        current.worker_failed.set()
    elif outcome == 'aligner_dead':
        manager._align_process = SimpleNamespace(is_alive=lambda: False, exitcode=1)
    if outcome in ('dead', 'failed', 'aligner_dead'):
        reason = {'dead': 'WorkerExited', 'failed': 'WorkerChannelFailed',
                  'aligner_dead': 'AlignerExited'}[outcome]
        with pytest.raises(ResultDeliveryError, match=reason):
            manager._check_runtime()
        manager._start_aligner_process.assert_not_called()
        return
    manager._check_runtime()
    assert clock.heartbeat == 0  # Recovery must not manufacture worker progress.
    clock.now += 5600 if outcome == 'repeated_gap' else 5
    if outcome in ('stalled', 'repeated_gap'):
        with pytest.raises(ResultDeliveryError, match='WorkerStalled'):
            manager._check_runtime()
    else:
        clock.heartbeat = clock.now
        manager._check_runtime()
        if outcome == 'second_sleep':
            clock.now += 5600
            manager._check_runtime()
            clock.now += 5
            clock.heartbeat = clock.now
            manager._check_runtime()
