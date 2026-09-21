"""Task-local inference failures across queue, protocol and client boundaries."""

import asyncio
import base64
from concurrent.futures import Future
import json
from pathlib import Path
import pickle
import queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.protocol import AudioMessage, ProtocolValidationError, RecognitionMessage
from core.server.schema import Result, Task
from core.server.state import WorkerState
from core.server.task_failures import FailedTasks
from core.server.worker.task_handler import TaskHandler
from core.server.connection.ws_recv import AudioCache, message_handler, ws_recv
from core.server.connection.ws_send import ws_send
from core.client.state import ClientState
from core.client.output.result_processor import ResultProcessor
from core.client.shortcut.task import ShortcutTask
from core.client.transcribe.file_transcriber import FileTranscriber


def make_task(socket='a', task_id='shared', *, final=False, capable=True):
    return Task('mic', b'\0' * 6400, 0, 0, task_id, socket, final, 1, 2,
                supports_task_errors=capable)


def audio(task_id='shared', *, final=False, capable=True):
    return AudioMessage(task_id, 'mic', base64.b64encode(b'\0' * 6400).decode(),
                        final, 1, seg_duration=0.1, seg_overlap=0,
                        supports_task_errors=capable)


def failure(socket='a', task_id='shared', *, capable=True):
    return Result(task_id, socket, 'mic', is_final=True, error_code='recognition_failed',
                  supports_task_errors=capable)


def wire_error(task_id='shared'):
    return RecognitionMessage(task_id, True, 0, 1, 2, 3, '', error_code='recognition_failed')


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setattr('core.server.worker.task_handler.GpuMemoryMonitor', Mock())
    monkeypatch.setattr('core.server.connection.ws_recv.status_mic', Mock())
    monkeypatch.setattr('core.server.connection.ws_recv.Config.gpu_boost_enabled', False)
    return TaskHandler(queue.Queue(), queue.Queue(), ['a', 'b'], WorkerState())


def server_state():
    return SimpleNamespace(queue_in=queue.Queue(), queue_out=queue.Queue(), sockets={},
                           sockets_id=[], socket_last_activity={}, audio_caches={},
                           failed_tasks=FailedTasks())


@pytest.mark.parametrize('final', [False, True])
def test_pipeline_failure_emits_one_empty_terminal_and_discards_only_its_fragments(handler, final):
    first = make_task(final=final)
    handler.buffer.enqueue(first)
    handler.buffer.enqueue(make_task(final=True))
    peer = make_task('b', final=True)
    handler.buffer.enqueue(peer)
    handler.state.sessions[first.key].result.text = 'synthetic private partial text'
    handler.pipeline = SimpleNamespace(process=Mock(side_effect=RuntimeError('synthetic secret')))
    assert handler.buffer.pop() is first
    handler.handle_audio_task(first)

    error = pickle.loads(pickle.dumps(handler.queue_out.get_nowait()))
    assert error.is_final and error.error_code == 'recognition_failed'
    assert (error.socket_id, error.task_id) == first.key
    assert error.supports_task_errors
    assert not error.text and not error.tokens and not error.text_accu
    assert 'synthetic' not in repr(error)
    assert set(handler.state.sessions) == {peer.key}
    assert handler.buffer.pop() is peer
    assert handler.buffer.is_empty
    handler.handle_audio_task(make_task(final=True))
    assert handler.queue_out.empty()
    handler.pipeline.process.assert_called_once()
    handler.gpu_monitor.end_task.assert_called_once()


def test_failure_leaves_same_connection_peer_runnable(handler):
    failed, peer = make_task(), make_task(task_id='next', final=True)
    handler.buffer.enqueue(failed)
    handler.buffer.enqueue(peer)
    handler.pipeline = SimpleNamespace(process=Mock(side_effect=[
        RuntimeError('synthetic'), Result('next', 'a', 'mic', is_final=True),
    ]))
    handler.handle_audio_task(handler.buffer.pop())
    handler.handle_audio_task(handler.buffer.pop())
    assert handler.queue_out.get_nowait().error_code == 'recognition_failed'
    success = handler.queue_out.get_nowait()
    assert success.task_id == 'next' and success.is_final and not success.error_code
    assert not handler.state.sessions and handler.buffer.is_empty


def test_late_input_does_not_resurrect_failed_session(handler):
    handler.state.failed_tasks.add('a', 'shared')
    healthy = make_task('a', 'next')
    handler.queue_in = Mock()
    handler.queue_in.get.side_effect = [make_task(final=True), healthy, queue.Empty]
    assert handler.drain_queue() is True
    assert handler.buffer.pop() is healthy
    assert set(handler.state.sessions) == {healthy.key}


def test_failure_budget_is_bounded_and_disconnect_releases_history(handler, monkeypatch):
    monkeypatch.setattr(FailedTasks, 'MAX_PER_CONNECTION', 2)
    handler.pipeline = SimpleNamespace(process=Mock(side_effect=ValueError('synthetic')))
    for task_id in ('one', 'two', 'three'):
        handler.handle_audio_task(make_task(task_id=task_id))
    first, last = handler.queue_out.get_nowait(), handler.queue_out.get_nowait()
    assert not first.close_connection and last.close_connection
    assert handler.queue_out.empty()
    assert handler.state.failed_tasks.tasks == {'a': {'one', 'two'}}
    handler.sockets_id.remove('a')
    handler.cleanup()
    assert not handler.state.failed_tasks.tasks and not handler.state.failed_tasks.blocked
    assert pickle.loads(pickle.dumps(FailedTasks())).tasks == {}


@pytest.mark.parametrize('final', [False, True])
def test_capability_survives_audio_parsing_cache_and_queue(handler, final):
    message = AudioMessage.from_dict(json.loads(audio(final=final).to_json()))
    state = server_state()
    asyncio.run(message_handler(SimpleNamespace(id='a'), message, AudioCache(message),
                                SimpleNamespace(state=state)))
    tasks = []
    while not state.queue_in.empty():
        tasks.append(state.queue_in.get_nowait())
    assert tasks and all(task.supports_task_errors for task in tasks)
    assert all(task.is_final == final for task in tasks)
    changed = audio(final=True, capable=False)
    with pytest.raises(ProtocolValidationError):
        AudioCache(message).validate_metadata(changed)


@pytest.mark.parametrize('capable', [None, 1, 'true', []])
def test_audio_capability_requires_boolean(capable):
    data = json.loads(audio().to_json())
    data['supports_task_errors'] = capable
    with pytest.raises(ProtocolValidationError):
        AudioMessage.from_dict(data)


def test_legacy_protocol_defaults_remain_success_and_no_error_capability():
    data = json.loads(audio().to_json())
    del data['supports_task_errors']
    assert AudioMessage.from_dict(data).supports_task_errors is False
    data = wire_error().to_dict()
    del data['error_code']
    assert RecognitionMessage.from_dict(data).error_code == ''
    assert RecognitionMessage.from_dict(json.loads(wire_error().to_json())).error_code == 'recognition_failed'


@pytest.mark.parametrize('overrides', [
    {'error_code': []}, {'error_code': 'arbitrary'}, {'is_final': False},
    {'is_final': 1}, {'task_id': ''}, {'task_id': 'bad\n'},
    {'text': 'synthetic private text'}, {'text_accu': 'partial'},
    {'tokens': ['partial']}, {'timestamps': [0]}, {'duration': float('nan')},
])
def test_error_message_rejects_invalid_or_content_bearing_payload(overrides):
    data = wire_error().to_dict()
    data.update(overrides)
    with pytest.raises(ProtocolValidationError):
        RecognitionMessage.from_dict(data)


@pytest.mark.parametrize('capable', [False, True])
def test_error_routes_to_owner_only_with_legacy_close_fallback(handler, capable):
    async def run():
        state = server_state()
        failed = SimpleNamespace(id='a', send=AsyncMock(), close=AsyncMock())
        peer = SimpleNamespace(id='b', send=AsyncMock(), close=AsyncMock())
        state.sockets = {'a': failed, 'b': peer}
        state.audio_caches = {'a': {'shared': AudioCache(audio())},
                              'b': {'shared': AudioCache(audio())}}
        for value in (failure(capable=capable), failure(capable=capable),
                      Result('shared', 'a', 'mic', text='late', is_final=True),
                      Result('shared', 'b', 'mic', text='synthetic peer', is_final=True), None):
            state.queue_out.put(value)
        await asyncio.wait_for(ws_send(SimpleNamespace(state=state)), 1)
        assert not state.audio_caches['a'] and 'shared' in state.audio_caches['b']
        peer.send.assert_awaited_once()
        peer.close.assert_not_awaited()
        if capable:
            failed.send.assert_awaited_once()
            failed.close.assert_not_awaited()
            decoded = RecognitionMessage.from_dict(json.loads(failed.send.await_args.args[0]))
            assert decoded.error_code == 'recognition_failed' and decoded.is_final
        else:
            failed.send.assert_not_awaited()
            failed.close.assert_awaited_once_with(code=1011, reason='Recognition task failed')

    asyncio.run(run())


def test_failure_budget_closes_connection_after_delivering_error(handler, monkeypatch):
    monkeypatch.setattr(FailedTasks, 'MAX_PER_CONNECTION', 1)

    async def run():
        state = server_state()
        socket = SimpleNamespace(id='a', send=AsyncMock(), close=AsyncMock())
        state.sockets = {'a': socket}
        state.queue_out.put(failure())
        state.queue_out.put(None)
        await ws_send(SimpleNamespace(state=state))
        socket.send.assert_awaited_once()
        socket.close.assert_awaited_once_with(code=1011, reason='Task failure limit reached; reconnect')

    asyncio.run(run())


@pytest.mark.parametrize('send_stalls', [False, True])
@pytest.mark.parametrize('close_stalls', [False, True])
def test_error_delivery_failure_closes_or_aborts_without_blocking_peer(
        handler, monkeypatch, send_stalls, close_stalls):
    monkeypatch.setattr('core.server.connection.ws_send.TASK_ERROR_IO_TIMEOUT', 0.01)

    async def run():
        async def stalled(*args, **kwargs):
            await asyncio.Event().wait()

        state = server_state()
        failed = SimpleNamespace(
            id='a', send=AsyncMock(side_effect=stalled if send_stalls else OSError('synthetic')),
            close=AsyncMock(side_effect=stalled if close_stalls else None), transport=Mock(),
        )
        peer = SimpleNamespace(id='b', send=AsyncMock(), close=AsyncMock())
        state.sockets = {'a': failed, 'b': peer}
        for value in (failure(), failure(), Result('shared', 'b', 'mic', is_final=True), None):
            state.queue_out.put(value)
        await asyncio.wait_for(ws_send(SimpleNamespace(state=state)), 1)
        failed.send.assert_awaited_once()
        failed.close.assert_awaited_once_with(code=1011, reason='Recognition task failed')
        assert failed.transport.abort.call_count == int(close_stalls)
        peer.send.assert_awaited_once()
        peer.close.assert_not_awaited()

    asyncio.run(run())


def test_legacy_close_failure_aborts_transport(handler, monkeypatch):
    async def run():
        state = server_state()
        socket = SimpleNamespace(id='a', send=AsyncMock(),
                                 close=AsyncMock(side_effect=OSError('synthetic')), transport=Mock())
        state.sockets = {'a': socket}
        state.queue_out.put(failure(capable=False))
        state.queue_out.put(None)
        await ws_send(SimpleNamespace(state=state))
        socket.send.assert_not_awaited()
        socket.transport.abort.assert_called_once()

    asyncio.run(run())


def test_receiver_drops_failed_task_tail_and_releases_registry_on_disconnect(handler):
    async def run():
        from websockets.exceptions import ConnectionClosedOK
        from websockets.frames import Close
        state = server_state()
        state.failed_tasks.add('a', 'shared')
        socket = SimpleNamespace(id='a', remote_address=('127.0.0.1', 1234), close=AsyncMock())
        socket.recv = AsyncMock(side_effect=[
            audio(final=True).to_json(), audio('next', final=True).to_json(),
            ConnectionClosedOK(Close(1000, ''), Close(1000, ''), True),
        ])
        await ws_recv(socket, SimpleNamespace(state=state))
        assert state.queue_in.get_nowait().task_id == 'next'
        assert state.queue_in.empty()
        assert not state.audio_caches and not state.failed_tasks.tasks
        assert not state.sockets and not state.sockets_id

    asyncio.run(run())


def test_file_server_error_does_not_wait_for_upload_or_save_partial_output(monkeypatch):
    async def run():
        ws = SimpleNamespace(receive=AsyncMock(), close=AsyncMock())
        transcriber = FileTranscriber(SimpleNamespace(ws=ws, state=ClientState()),
                                      Path('synthetic.wav'), output_formats=frozenset({'txt'}))
        ws.receive.return_value = wire_error(transcriber.task_id)
        save = Mock()
        monkeypatch.setattr('core.client.transcribe.file_transcriber.ResultHandler.save_results', save)
        assert not transcriber._send_complete.is_set()
        assert await asyncio.wait_for(transcriber.receive(), 1) is False
        assert transcriber.failure_code == 'recognition_failed'
        save.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize('active_id', ['shared', 'newer', None])
def test_dictation_error_cleans_only_its_owner_and_never_outputs_text(monkeypatch, active_id):
    async def run():
        for name in ('Status', 'hide_recording_indicator', 'hide_status_hint', 'set_recording_state'):
            monkeypatch.setattr(f'core.client.shortcut.task.{name}', Mock())
        hint = Mock()
        monkeypatch.setattr('core.ui.show_status_hint', hint)
        state = ClientState()
        state.task_contexts = {'shared': ('synthetic context', 0), 'newer': ('next', 0)}
        state.audio_files = {'shared': Path('synthetic.wav'), 'newer': Path('next.wav')}
        state.last_output_text = 'previous'
        old_future = Future()
        state.recorder_by_id['shared'] = old_future
        app = SimpleNamespace(state=state, progress=Mock(), mark_user_activity=Mock(),
                              llm=SimpleNamespace(process=AsyncMock()),
                              output=SimpleNamespace(output=AsyncMock()))
        if active_id:
            owner = ShortcutTask(app, SimpleNamespace(key='ctrl_r'))
            owner._progress_id = active_id
            owner._capture = Mock()
            owner.task = old_future if active_id == 'shared' else Future()
            owner.is_recording = True
            state.recording_owner = owner
            state.capture = owner._capture
            state.recording = True
        processor = ResultProcessor(app)
        processor._save = Mock()
        await processor._handle_message(wire_error())
        await processor._handle_message(wire_error())
        assert 'shared' not in state.task_contexts and 'newer' in state.task_contexts
        assert 'shared' not in state.audio_files and 'newer' in state.audio_files
        assert old_future.cancelled()
        assert state.last_output_text == 'previous'
        app.llm.process.assert_not_awaited()
        app.output.output.assert_not_awaited()
        processor._save.assert_not_called()
        if active_id == 'newer':
            assert state.recording and state.recording_owner is owner
            assert not owner.task.cancelled()
            hint.assert_not_called()
        else:
            assert state.recording_owner is None and not state.recording
            hint.assert_called_once()

    asyncio.run(run())
