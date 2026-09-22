"""Whole-item regressions for cancellation, lost progress and shutdown ownership."""

import asyncio
import base64
import json
from multiprocessing import Value, get_context
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from core.protocol import AudioMessage, CancelMessage, ProtocolValidationError, RecognitionMessage
from core.client.audio.recorder import AudioRecorder
from core.client.dictation_lifecycle import cancel_dictation
from core.client.output.result_processor import ResultProcessor
from core.client.state import ClientState
from core.server.app import CapsWriterServer
from core.server.connection.ws_recv import ws_recv
from core.server.connection.ws_send import ws_send
from core.server.delivery import ResultDeliveryError
from core.server.engines.manager import ProcessAlignerProxy
from core.server.schema import AlignResponse, Result, Task
from core.server.state import WorkerState
from core.server.task_failures import FailedTasks
from core.server.worker.process_manager import ProcessManager
from core.server.worker.supervision import progress
from core.server.worker.task_handler import TaskHandler


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr('core.server.worker.task_handler.GpuMemoryMonitor', Mock())
    monkeypatch.setattr('core.server.connection.ws_recv.status_mic', Mock())
    monkeypatch.setattr('core.server.connection.ws_recv.Config.gpu_boost_enabled', False)
    monkeypatch.setattr('core.client.audio.recorder.Config.save_audio', False)
    monkeypatch.setattr('core.client.audio.recorder.Config.threshold', 0)
    monkeypatch.setattr('core.client.dictation_lifecycle.CLOSE_TIMEOUT', 0.01)
    monkeypatch.setattr('core.ui.show_status_hint', Mock())


@pytest.mark.parametrize('task_id', ['', 42, '\n', 'x' * 129])
def test_cancel_identity_is_validated(task_id):
    with pytest.raises(ProtocolValidationError):
        CancelMessage.from_dict({'type': 'cancel', 'task_id': task_id})


def test_cancellation_wire_round_trip_and_empty_terminal_contract():
    with pytest.raises(ProtocolValidationError):
        CancelMessage.from_dict([])
    assert CancelMessage.from_dict(json.loads(CancelMessage('task').to_json())).task_id == 'task'
    result = RecognitionMessage('task', True, 0, 0, 0, 0, '', error_code='cancelled')
    assert RecognitionMessage.from_dict(result.to_dict()).error_code == 'cancelled'
    result.text = 'must not leak'
    with pytest.raises(ProtocolValidationError):
        RecognitionMessage.from_dict(result.to_dict())


def make_task(socket_id='a', task_id='task', command=''):
    return Task('cmd' if command else 'mic', b'', 0, 0, task_id, socket_id,
                False, 0, 0, command=command)


def test_cancel_discards_worker_buffers_and_late_fragments_but_preserves_peer():
    worker = TaskHandler(queue.Queue(), queue.Queue(), ['a', 'b'], WorkerState())
    for task in (make_task(), make_task('b'), make_task(command='cancel'), make_task()):
        worker.queue_in.put(task)
    assert worker.drain_queue()
    worker.cleanup()
    assert set(worker.state.sessions) == {('b', 'task')}
    assert worker.buffer.pop().socket_id == 'b'
    assert worker.buffer.is_empty
    assert worker.state.failed_tasks.contains('a', 'task')


def test_server_cancellation_ack_is_once_and_late_results_are_suppressed():
    async def run():
        current = SimpleNamespace(queue_in=queue.Queue(), queue_out=queue.Queue(),
                                  sockets={}, sockets_id=[], socket_last_activity={},
                                  audio_caches={}, failed_tasks=FailedTasks())
        client = SimpleNamespace(id='a', remote_address=('127.0.0.1', 1),
                                 send=AsyncMock(), close=AsyncMock(), transport=Mock())
        app = SimpleNamespace(state=current)
        messages = iter([CancelMessage('task').to_json(), CancelMessage('task').to_json(),
                         AudioMessage('task', 'mic', base64.b64encode(b'\0' * 4).decode(), True, 0).to_json()])

        async def recv():
            raw = next(messages, None)
            if raw is not None:
                return raw
            current.queue_out.put(Result('task', 'a', 'mic', is_final=True, text='late'))
            current.queue_out.put(Result('peer', 'a', 'mic', is_final=True, text='peer'))
            current.queue_out.put(None)
            await ws_send(app)
            assert [json.loads(call.args[0])['task_id'] for call in client.send.call_args_list] == ['task', 'peer']
            assert current.queue_in.qsize() == 1
            assert current.queue_in.get_nowait().command == 'cancel'
            return '{}'  # End the synthetic receiver via its validation boundary.

        client.recv = recv
        await ws_recv(client, app)
        assert not current.audio_caches and not current.sockets_id

    asyncio.run(run())


def test_partial_recording_cancel_sends_only_its_task_id():
    async def run():
        state = ClientState()
        client = SimpleNamespace(send=AsyncMock(), close=AsyncMock(), transport=Mock())
        state.websocket = client
        app = SimpleNamespace(state=state, progress=Mock(),
                              ws=SimpleNamespace(is_connected=True, send=AsyncMock(return_value=True)),
                              caret_context=SimpleNamespace(capture=AsyncMock(return_value='')))
        recorder = AudioRecorder(app)
        for item in ({'type': 'begin', 'time': 1},
                     {'type': 'data', 'time': 2, 'data': np.zeros((480, 1), dtype=np.float32)},
                     {'type': 'cancel'}):
            state.queue_in.put_nowait(item)
        with pytest.raises(asyncio.CancelledError):
            await recorder.record_and_send()
        assert json.loads(client.send.call_args.args[0]) == {'task_id': recorder.task_id, 'type': 'cancel'}
        client.close.assert_not_awaited()
        assert not state.task_contexts and not state.dictation_uploads

    asyncio.run(run())


def test_deadline_cancels_remote_task_once_without_canceling_peer():
    async def run():
        state = ClientState()
        client = SimpleNamespace(send=AsyncMock(), close=AsyncMock(), transport=Mock())
        state.websocket = client
        for task_id in ('expired', 'peer'):
            state.task_contexts[task_id] = ('', 0)
            state.dictation_deadlines[task_id] = time.monotonic() + (-1 if task_id == 'expired' else 30)
        processor = ResultProcessor(SimpleNamespace(state=state, progress=Mock()))
        processor._expire_tasks()
        processor._expire_tasks()
        await asyncio.gather(*processor._cancellations)
        assert json.loads(client.send.call_args.args[0])['task_id'] == 'expired'
        client.send.assert_awaited_once()
        assert set(state.task_contexts) == {'peer'}

    asyncio.run(run())


def test_cancel_send_timeout_closes_captured_connection_without_replacing_new_one():
    async def run():
        client = SimpleNamespace(close=AsyncMock(), transport=Mock())
        state = SimpleNamespace(websocket=client)

        async def send(_):
            await asyncio.Event().wait()

        client.send = send
        await asyncio.wait_for(cancel_dictation(state, client, 'task'), 1)
        client.close.assert_awaited_once()
        replacement = object()
        state.websocket = replacement
        await cancel_dictation(state, client, 'task')
        assert state.websocket is replacement

    asyncio.run(run())


@pytest.mark.parametrize('mode', ['stall', 'broken', 'read_timeout_error', 'silent_loss'])
def test_startup_deadline_or_channel_error_stops_once(monkeypatch, mode):
    monkeypatch.setattr('core.server.worker.process_manager.Config.model_startup_timeout', 0.02, raising=False)
    release = threading.Event()

    def read(**kwargs):
        if mode == 'stall':
            assert release.wait(1)
        if mode == 'broken':
            raise OSError('synthetic')
        if mode == 'read_timeout_error':
            raise TimeoutError('synthetic read failure')
        raise queue.Empty

    current = SimpleNamespace(worker_failed=threading.Event(), queue_out=Mock(get=Mock(side_effect=read)))
    app = SimpleNamespace(state=current, stop=Mock())
    manager = ProcessManager(app)
    manager.is_alive = True
    manager._process = SimpleNamespace(is_alive=lambda: True)
    start = time.monotonic()
    try:
        manager._wait_for_models()
        app.stop.assert_called_once()
        assert time.monotonic() - start < 0.5
        if mode != 'silent_loss':
            current.queue_out.get.assert_called_once()
    finally:
        release.set()


@pytest.mark.parametrize('mode', ['asr_dead', 'asr_stalled', 'aligner_dead', 'signaled'])
def test_runtime_failure_stops_without_restarting_broken_worker(mode):
    current = SimpleNamespace(worker_failed=threading.Event(), worker_progress=Value('d', time.monotonic()))
    manager = ProcessManager(SimpleNamespace(state=current))
    manager.is_alive = True
    manager._process = SimpleNamespace(is_alive=lambda: mode != 'asr_dead')
    manager._align_process = SimpleNamespace(is_alive=lambda: mode != 'aligner_dead', exitcode=1)
    manager._start_aligner_process = Mock()
    if mode == 'asr_stalled':
        current.worker_progress.value = time.monotonic() - 100000
    if mode == 'signaled':
        current.worker_failed.set()
    with pytest.raises(ResultDeliveryError):
        manager._check_runtime()
    manager._start_aligner_process.assert_not_called()


def test_clean_aligner_idle_exit_can_still_be_replaced():
    current = SimpleNamespace(worker_failed=threading.Event(), worker_progress=Value('d', time.monotonic()))
    manager = ProcessManager(SimpleNamespace(state=current))
    manager._process = SimpleNamespace(is_alive=lambda: True)
    manager._align_process = SimpleNamespace(is_alive=lambda: False, exitcode=0)
    manager._start_aligner_process = Mock()
    manager._check_runtime()
    manager._start_aligner_process.assert_called_once()


def test_dead_progress_lock_is_bounded():
    clock = SimpleNamespace(get_lock=lambda: Mock(acquire=Mock(return_value=False)))
    with pytest.raises(ResultDeliveryError, match='WorkerProgressUnavailable'):
        progress(clock)


def test_idle_worker_updates_progress_and_does_not_time_out_without_tasks():
    clock = Value('d', 0)
    worker = TaskHandler(queue.Queue(), queue.Queue(), [], WorkerState(), progress_clock=clock)
    worker.queue_in.put(None)
    assert not worker.drain_queue()
    assert time.monotonic() - progress(clock) < 1
    worker.cleanup()


def test_lost_final_and_cancel_cannot_leave_immortal_session():
    failure = threading.Event()
    worker = TaskHandler(queue.Queue(), queue.Queue(), ['a'], WorkerState(), failure)
    worker.buffer.enqueue(make_task())
    worker.session_activity[('a', 'task')] = time.monotonic() - worker.session_timeout - 1
    worker.drain_queue = Mock(return_value=True)
    with pytest.raises(ResultDeliveryError, match='TaskProgressTimeout'):
        worker.loop()
    assert failure.is_set() and not worker.state.sessions and worker.buffer.is_empty


@pytest.mark.parametrize('mode', ['timeout', 'broken_input', 'broken_output'])
def test_aligner_fault_signals_service_shutdown(mode):
    failure = threading.Event()
    incoming, outgoing = queue.Queue(), queue.Queue()
    if mode == 'broken_input':
        incoming = Mock(put=Mock(side_effect=OSError('synthetic')))
    if mode == 'broken_output':
        outgoing = Mock(get=Mock(side_effect=OSError('synthetic')))
    proxy = ProcessAlignerProxy(incoming, outgoing, 0.01, failure)
    with pytest.raises(ResultDeliveryError):
        proxy.align(b'', 'synthetic')
    assert failure.is_set()


def test_caught_alignment_error_still_allows_existing_timestamp_fallback():
    failure = threading.Event()
    outgoing = queue.Queue()

    def put(request, **kwargs):
        outgoing.put(AlignResponse(request.request_id, request.task_id, error='SyntheticError'))

    proxy = ProcessAlignerProxy(SimpleNamespace(put=put), outgoing, 1, failure)
    assert proxy.align(b'', 'synthetic') is None
    assert not failure.is_set()


def test_foreign_thread_exit_closes_network_on_loop_owner_before_reaping():
    app = CapsWriterServer.__new__(CapsWriterServer)
    app._owner_thread = threading.get_ident()
    app._stop_requested = threading.Event()
    app._cleaned_up = False
    app.is_alive = True
    app.state = SimpleNamespace(queue_out=queue.Queue())
    calls = []
    app.socket_manager = SimpleNamespace(stop=lambda: calls.append(('network', threading.get_ident())))
    app.process_manager = SimpleNamespace(stop=lambda: calls.append(('worker', threading.get_ident())))
    app.tray_manager = SimpleNamespace(stop=lambda: calls.append(('tray', threading.get_ident())))
    app.loop = asyncio.new_event_loop()

    async def run():
        await asyncio.to_thread(app.stop)
        await asyncio.sleep(0)
        assert not app.is_alive
        assert calls == [('network', app._owner_thread)]
        assert app.loop.is_running()  # No abrupt loop.stop while handlers are draining.

    try:
        app.loop.run_until_complete(run())
        app._cleanup()
        app._cleanup()
        assert [name for name, _ in calls].count('worker') == 1
        assert all(owner == app._owner_thread for _, owner in calls)
    finally:
        app.loop.close()


@pytest.mark.parametrize('foreign', [False, True])
def test_exit_during_startup_skips_network_and_cleans_once(monkeypatch, foreign):
    app = CapsWriterServer.__new__(CapsWriterServer)
    app.is_alive = False
    app.state = SimpleNamespace(queue_out=queue.Queue())
    app.loop = asyncio.new_event_loop()

    def startup():
        if foreign:
            request = threading.Thread(target=app.stop)
            request.start()
            request.join(1)
        else:
            app.stop()  # A signal can interrupt startup on its own thread.
        assert app._stop_requested.is_set()
        app.process_manager.stop.assert_not_called()

    app.process_manager = SimpleNamespace(start=startup, stop=Mock())
    app.socket_manager = SimpleNamespace(prepare=Mock(), start=AsyncMock(), stop=Mock())
    app.tray_manager = SimpleNamespace(start=Mock(), stop=Mock())
    app._print_banner = Mock()
    monkeypatch.setattr('core.server.app.register_signal', Mock())
    try:
        app.start()
        app.socket_manager.start.assert_not_awaited()
        app.process_manager.stop.assert_called_once()
        app.tray_manager.stop.assert_called_once()
    finally:
        app.loop.close()


def test_repeated_recorder_cancellation_still_finishes_owned_cleanup(monkeypatch):
    monkeypatch.setattr('core.client.dictation_lifecycle.CLOSE_TIMEOUT', 1)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        state = ClientState()

        async def send(_):
            entered.set()
            await release.wait()

        state.websocket = SimpleNamespace(send=send, close=AsyncMock(), transport=Mock())
        app = SimpleNamespace(state=state, progress=Mock())
        recorder = AudioRecorder(app)
        recorder._websocket = state.websocket
        recorder._writer = SimpleNamespace(close=AsyncMock())
        state.queue_in.put_nowait({'type': 'cancel'})
        operation = asyncio.create_task(recorder.record_and_send())
        await asyncio.wait_for(entered.wait(), 1)
        operation.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 1)
        recorder._writer.close.assert_awaited_once_with(abort=True)
        assert not state.dictation_uploads

    asyncio.run(run())


def test_shutdown_cancels_and_joins_inflight_cancellation_sends(monkeypatch):
    monkeypatch.setattr('core.client.dictation_lifecycle.CLOSE_TIMEOUT', 1)
    async def run():
        entered = asyncio.Event()
        state = ClientState()

        async def send(_):
            entered.set()
            await asyncio.Event().wait()

        client = SimpleNamespace(send=send, close=AsyncMock(), transport=Mock())
        state.websocket = client
        state.task_contexts['expired'] = ('', 0)
        state.dictation_deadlines['expired'] = time.monotonic() - 1
        app = SimpleNamespace(state=state, progress=Mock())
        processor = ResultProcessor(app)
        processor._receive_loop = lambda: asyncio.Event().wait()
        operation = asyncio.create_task(processor.start())
        processor._expire_tasks()
        await asyncio.wait_for(entered.wait(), 1)
        processor._exit_event.set()
        await asyncio.wait_for(operation, 1)
        assert not processor._cancellations
        client.transport.abort.assert_called_once()

    asyncio.run(run())


def synthetic_stalled_worker(clock, ready):
    """Spawnable synthetic child: no engines, microphone, network or GPU calls."""
    progress(clock, update=True)
    ready.set()
    threading.Event().wait(30)


def test_spawned_stalled_child_is_detected_terminated_and_reaped():
    context = get_context('spawn')
    clock, ready = context.Value('d', 0), context.Event()
    process = context.Process(target=synthetic_stalled_worker, args=(clock, ready), daemon=True)
    current = SimpleNamespace(worker_progress=clock, worker_failed=context.Event(), queue_in=queue.Queue())
    manager = ProcessManager(SimpleNamespace(state=current))
    manager.is_alive = True
    manager._process = process
    try:
        process.start()
        assert ready.wait(10)
        clock.value = time.monotonic() - 100000
        with pytest.raises(ResultDeliveryError, match='WorkerStalled'):
            manager._check_runtime()
        manager.stop()
        assert not process.is_alive() and process.exitcode is not None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        process.close()


def test_partial_input_expires_even_while_peer_task_keeps_connection_active():
    async def run():
        current = SimpleNamespace(queue_in=queue.Queue(), sockets={}, sockets_id=[],
                                  socket_last_activity={}, audio_caches={}, failed_tasks=FailedTasks())
        client = SimpleNamespace(id='a', remote_address=('127.0.0.1', 1), close=AsyncMock())
        count = 0

        async def recv():
            nonlocal count
            count += 1
            if count == 2:
                current.audio_caches['a']['orphan'].last_activity = time.monotonic() - 100000
            return AudioMessage('orphan' if count == 1 else 'peer', 'mic',
                                base64.b64encode(b'\0' * 4).decode(), False, 0).to_json()

        client.recv = recv
        await asyncio.wait_for(ws_recv(client, SimpleNamespace(state=current)), 1)
        assert count == 2 and not current.audio_caches
        client.close.assert_awaited_once_with(code=1008, reason='Task input timed out; reconnect')

    asyncio.run(run())


def test_aligner_cleanup_error_does_not_skip_asr_or_registry_cleanup():
    current = SimpleNamespace(queue_in=queue.Queue(), align_queue_in=queue.Queue())
    manager = ProcessManager(SimpleNamespace(state=current))
    manager.is_alive = True
    manager._align_process = SimpleNamespace(is_alive=lambda: True, join=Mock(side_effect=OSError('synthetic')))
    manager._process = SimpleNamespace(is_alive=Mock(side_effect=[True, True, False]),
                                       join=Mock(), terminate=Mock())
    registry = SimpleNamespace(shutdown=Mock())
    manager._manager = registry
    manager.stop()
    manager._process.terminate.assert_called_once()
    registry.shutdown.assert_called_once()
