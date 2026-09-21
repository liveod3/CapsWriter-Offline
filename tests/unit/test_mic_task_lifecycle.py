"""Synthetic microphone deadlines, result ownership and serial output lifecycle."""

import asyncio
from concurrent.futures import Future
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from core.client.audio.recorder import AudioRecorder
from core.client.connection import CommunicationError
from core.client.dictation_lifecycle import (
    MAX_PENDING_DICTATIONS, DictationSendError, close_dictation_connection, dictation_timeouts,
)
from core.client.output.result_processor import ResultProcessor
from core.client.state import ClientState
from core.protocol import RecognitionMessage


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    monkeypatch.setattr('core.ui.show_status_hint', Mock())
    monkeypatch.setattr('core.client.audio.recorder.Config.save_audio', False)
    monkeypatch.setattr('core.client.audio.recorder.Config.threshold', 0)
    monkeypatch.setattr('core.client.dictation_lifecycle.CLOSE_TIMEOUT', 0.01)


def make_app():
    incoming = asyncio.Queue()
    app = SimpleNamespace(
        state=ClientState(), progress=Mock(), mark_user_activity=Mock(),
        caret_context=SimpleNamespace(capture=AsyncMock(return_value='synthetic context')),
        ws=SimpleNamespace(is_connected=True, send=AsyncMock(return_value=True),
                           connect=AsyncMock(return_value=True), receive=incoming.get),
    )
    return app, incoming


def pending(app, task_id, *, complete=True, delay=10):
    upload = asyncio.Event()
    if complete:
        upload.set()
    app.state.dictation_uploads[task_id] = upload
    app.state.dictation_deadlines[task_id] = time.monotonic() + delay
    app.state.task_contexts[task_id] = ('synthetic context', 0)
    return upload


def result(task_id, *, error=False, final=True):
    return RecognitionMessage(task_id, final, 0.1, 0, 0, 0,
                              '' if error else 'synthetic',
                              error_code='recognition_failed' if error else '')


async def until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.parametrize('value', [None, False, 0, -1, float('nan'), float('inf'), 'invalid'])
def test_invalid_timeouts_use_compatible_defaults(value):
    assert dictation_timeouts(SimpleNamespace(mic_io_timeout=value, mic_result_timeout=value)) == (60, 600)
    assert dictation_timeouts(SimpleNamespace()) == (60, 600)


@pytest.mark.parametrize('final', [False, True])
@pytest.mark.parametrize('failure', ['stall', 'false', 'exception', 'disconnected'])
def test_failed_upload_has_deadline_and_discards_pending_state(final, failure):
    async def run():
        app, _ = make_app()
        socket = SimpleNamespace(close=AsyncMock(), transport=Mock())
        app.state.websocket = socket
        recorder = AudioRecorder(app)
        recorder._io_timeout = 0.01
        for item in ({'type': 'begin', 'time': 1},
                     {'type': 'data', 'time': 2, 'data': np.zeros((480, 1), dtype=np.float32)},
                     {'type': 'finish'}):
            app.state.queue_in.put_nowait(item)

        async def send(message):
            if message.is_final != final:
                if failure == 'disconnected':
                    app.ws.is_connected = False
                return True
            if failure == 'stall':
                await asyncio.Event().wait()
            if failure == 'exception':
                raise OSError('synthetic sensitive exception text')
            return False

        app.ws.send.side_effect = send
        if failure == 'disconnected' and not final:
            app.ws.is_connected = False
        with pytest.raises(DictationSendError):
            await asyncio.wait_for(recorder.record_and_send(), 1)
        assert not app.state.task_contexts
        assert not app.state.dictation_uploads and not app.state.dictation_deadlines
        assert not app.state.audio_files
        app.progress.finish.assert_called_with(recorder.task_id)
        assert socket.close.await_count == int(failure != 'disconnected')

    asyncio.run(run())


def test_final_result_wait_starts_at_final_submission_not_during_recording():
    async def run():
        app, _ = make_app()
        recorder = AudioRecorder(app)
        final_started, release = asyncio.Event(), asyncio.Event()

        async def send(message):
            if message.is_final:
                assert recorder.task_id in app.state.dictation_deadlines
                final_started.set()
                await release.wait()
            else:
                assert recorder.task_id not in app.state.dictation_deadlines
            return True

        app.ws.send.side_effect = send
        for item in ({'type': 'begin', 'time': 1},
                     {'type': 'data', 'time': 2, 'data': np.zeros((480, 1), dtype=np.float32)},
                     {'type': 'finish'}):
            app.state.queue_in.put_nowait(item)
        operation = asyncio.create_task(recorder.record_and_send())
        await asyncio.wait_for(final_started.wait(), 1)
        processor = ResultProcessor(app)
        processor._handle_final = AsyncMock()
        delivery = asyncio.create_task(processor._handle_message(result(recorder.task_id)))
        await until(lambda: bool(processor._received))
        processor._handle_final.assert_not_awaited()
        release.set()
        await asyncio.wait_for(asyncio.gather(operation, delivery), 1)
        processor._handle_final.assert_awaited_once()
        assert not app.state.dictation_uploads and not app.state.task_contexts

    asyncio.run(run())


@pytest.mark.parametrize('via_message', [False, True])
def test_timeout_rejects_late_and_foreign_results_and_preserves_peer(via_message):
    async def run():
        app, _ = make_app()
        processor = ResultProcessor(app)
        processor._handle_final = AsyncMock()
        pending(app, 'old', delay=-1)
        pending(app, 'peer')
        old_future = Future()
        app.state.recorder_by_id['old'] = old_future
        if via_message:
            await processor._handle_message(result('old'))
        else:
            processor._expire_tasks()
        await processor._handle_message(result('old'))
        await processor._handle_message(result('foreign'))
        assert old_future.cancelled()
        assert set(app.state.task_contexts) == {'peer'}
        assert set(app.state.dictation_uploads) == {'peer'}
        assert set(app.state.dictation_deadlines) == {'peer'}
        processor._handle_final.assert_not_awaited()
        app.progress.finish.assert_called_once_with('old')

    asyncio.run(run())


def test_receiver_and_deadlines_continue_during_serial_text_processing():
    async def run():
        app, incoming = make_app()
        processor = ResultProcessor(app)
        processor._deadline_poll = 0.005
        for task_id in ('first', 'second', 'failed', 'stalled'):
            pending(app, task_id, delay=0.1)
        entered, release = asyncio.Event(), asyncio.Event()
        order = []

        async def process(message):
            order.append(message.task_id)
            if message.task_id == 'first':
                entered.set()
                await release.wait()

        processor._handle_final = process
        operation = asyncio.create_task(processor.start())
        incoming.put_nowait(result('first'))
        await asyncio.wait_for(entered.wait(), 1)
        for message in (result('second'), result('second'), result('foreign'),
                        result('failed', error=True)):
            incoming.put_nowait(message)
        await until(lambda: 'second' in processor._received)
        await until(lambda: 'stalled' not in app.state.dictation_uploads)
        assert 'failed' not in app.state.dictation_uploads
        assert order == ['first']
        assert set(app.state.dictation_uploads) == {'first', 'second'}
        release.set()
        await until(lambda: not app.state.dictation_uploads)
        assert order == ['first', 'second']
        processor.request_exit()
        await asyncio.wait_for(operation, 1)
        assert not processor._received and processor._ready_results.empty()
        assert not [task for task in asyncio.all_tasks()
                    if task is not asyncio.current_task() and not task.done()]

    asyncio.run(run())


def test_disconnect_fails_only_unfinished_recognition_and_keeps_accepted_output():
    async def run():
        app, _ = make_app()
        processor = ResultProcessor(app)
        for task_id in ('accepted', 'waiting'):
            pending(app, task_id)
        await processor._handle_message(result('accepted'), enqueue=True)
        old_future = Future()
        app.state.recorder_by_id['waiting'] = old_future
        app.state.websocket = SimpleNamespace(close=AsyncMock())
        app.ws.receive = AsyncMock(side_effect=CommunicationError('synthetic'))
        app.ws.connect = AsyncMock(side_effect=[True, False])
        reader = asyncio.create_task(processor._receive_loop())
        await until(lambda: old_future.cancelled())
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        assert set(app.state.dictation_uploads) == {'accepted'}
        processor._handle_final = AsyncMock()
        await processor._process_final(processor._ready_results.get_nowait())
        processor._handle_final.assert_awaited_once()
        assert not app.state.dictation_uploads

    asyncio.run(run())


@pytest.mark.parametrize('upload_succeeds', [False, True])
def test_final_received_before_upload_completion_cannot_output_failed_upload(upload_succeeds):
    async def run():
        app, _ = make_app()
        upload = pending(app, 'task', complete=False)
        processor = ResultProcessor(app)
        processor._handle_final = AsyncMock()
        processing = asyncio.create_task(processor._handle_message(result('task')))
        await until(lambda: 'task' in processor._received)
        processor._handle_final.assert_not_awaited()
        if not upload_succeeds:
            app.state.dictation_uploads.pop('task')
        upload.set()
        await asyncio.wait_for(processing, 1)
        assert processor._handle_final.await_count == int(upload_succeeds)
        assert not app.state.task_contexts and not app.state.dictation_deadlines

    asyncio.run(run())


def test_close_timeout_aborts_only_captured_connection():
    async def run():
        state = ClientState()

        async def close():
            await asyncio.Event().wait()

        old = SimpleNamespace(close=close, transport=Mock())
        newer = SimpleNamespace(transport=Mock())
        state.websocket = newer
        await asyncio.wait_for(close_dictation_connection(state, old), 1)
        old.transport.abort.assert_called_once()
        newer.transport.abort.assert_not_called()
        assert state.websocket is newer

    asyncio.run(run())


def test_cancelling_connection_cleanup_aborts_before_forgetting_socket():
    async def run():
        state = ClientState()
        closing = asyncio.Event()

        async def close():
            closing.set()
            await asyncio.Event().wait()

        socket = SimpleNamespace(close=close, transport=Mock())
        state.websocket = socket
        operation = asyncio.create_task(close_dictation_connection(state, socket))
        await asyncio.wait_for(closing.wait(), 1)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        socket.transport.abort.assert_called_once()
        assert state.websocket is None

    asyncio.run(run())


def test_capacity_rejects_new_recorder_without_evicting_pending_context():
    async def run():
        app, _ = make_app()
        for index in range(MAX_PENDING_DICTATIONS):
            pending(app, str(index))
        before = dict(app.state.task_contexts)
        recorder = AudioRecorder(app)
        with pytest.raises(DictationSendError):
            await recorder.record_and_send()
        assert app.state.task_contexts == before
        assert len(app.state.dictation_uploads) == MAX_PENDING_DICTATIONS
        app.caret_context.capture.assert_not_awaited()
        app.ws.send.assert_not_awaited()

    asyncio.run(run())


def test_exit_joins_reader_watcher_and_serial_processor():
    async def run():
        app, incoming = make_app()
        processor = ResultProcessor(app)
        pending(app, 'processing')
        pending(app, 'queued')
        pending(app, 'waiting')
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def process(_message):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        processor._handle_final = process
        operation = asyncio.create_task(processor.start())
        incoming.put_nowait(result('processing'))
        await asyncio.wait_for(entered.wait(), 1)
        incoming.put_nowait(result('queued'))
        await until(lambda: 'queued' in processor._received)
        processor.request_exit()
        await asyncio.wait_for(operation, 1)
        assert cancelled.is_set()
        assert not app.state.dictation_uploads and not app.state.dictation_deadlines
        assert not app.state.task_contexts and not processor._received
        assert processor._ready_results.empty()
        assert not [task for task in asyncio.all_tasks()
                    if task is not asyncio.current_task() and not task.done()]

    asyncio.run(run())
