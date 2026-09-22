"""Connection collisions using synthetic audio, fake ASR and in-memory transport."""

import asyncio
import base64
import json
import pickle
import queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from core.protocol import AudioMessage
from core.server.connection.ws_recv import AudioCache, message_handler
from core.server.connection.ws_send import ws_send
from core.server.engines.base import EngineCapabilities
from core.server.schema import Task
from core.server.state import WorkerState
from core.server.worker.task_handler import TaskBuffer, TaskHandler


def make_task(socket_id, sequence=0, *, final=False, task_id='shared'):
    return Task(
        type='mic', data=b'', offset=sequence * 0.1, overlap=0.0,
        task_id=task_id, socket_id=socket_id, is_final=final,
        time_start=0.0, time_submit=float(sequence),
    )


@pytest.fixture
def handler(monkeypatch):
    # No GPU commands, monitoring threads, microphone or models in these tests.
    monkeypatch.setattr('core.server.worker.task_handler.GpuMemoryMonitor', Mock())
    monkeypatch.setattr('core.server.worker.task_handler.Config.gpu_boost_enabled', False)
    monkeypatch.setattr('core.server.connection.ws_recv.status_mic', Mock())
    return TaskHandler(queue.Queue(), queue.Queue(), ['a', 'b'], WorkerState())


def test_same_id_has_independent_sessions_and_mutable_results():
    state = WorkerState()
    a = state.get_session('shared', 'a', 'mic')
    b = state.get_session('shared', 'b', 'file')
    a.result.text = 'alpha'
    a.result.tokens.append('alpha')
    a.result.timestamps.append(0.0)
    a.result.duration = 1.0

    assert state.get_session('shared', 'a', 'mic') is a
    assert b is not a
    assert (b.result.socket_id, b.result.task_id, b.result.type) == ('b', 'shared', 'file')
    assert (b.result.text, b.result.tokens, b.result.timestamps, b.result.duration) == (
        '', [], [], 0.0,
    )
    assert set(state.sessions) == {('a', 'shared'), ('b', 'shared')}


def test_connection_scoped_identity_survives_pickle():
    task = make_task('a')
    assert pickle.loads(pickle.dumps(task)).key == ('a', 'shared')
    state = WorkerState()
    state.get_session(task.task_id, task.socket_id, task.type)
    restored = pickle.loads(pickle.dumps(state))
    assert restored.sessions[task.key].result.socket_id == 'a'


def test_colliding_ids_preserve_fifo_and_round_robin_fairness():
    buffer = TaskBuffer(WorkerState())
    for socket in ('a', 'b'):
        for sequence in range(3):
            buffer.enqueue(make_task(socket, sequence))

    assert [(task.socket_id, task.time_submit) for task in
            [buffer.pop() for _ in range(6)]] == [
        ('a', 0.0), ('b', 0.0), ('a', 1.0), ('b', 1.0), ('a', 2.0), ('b', 2.0),
    ]
    assert buffer.is_empty


@pytest.mark.parametrize('disconnected', ['a', 'b'])
def test_disconnect_removes_only_owned_sessions_and_fragments(disconnected):
    state = WorkerState()
    buffer = TaskBuffer(state)
    survivor = 'b' if disconnected == 'a' else 'a'
    for socket in ('a', 'b'):
        for task_id in ('shared', 'other'):
            for sequence in range(2):
                buffer.enqueue(make_task(socket, sequence, task_id=task_id))

    assert state.cleanup_sessions([survivor]) == 2
    buffer.cleanup_tasks()
    assert buffer.task_count == 4
    assert set(state.sessions) == {(survivor, 'shared'), (survivor, 'other')}
    assert all(buffer.pop().socket_id == survivor for _ in range(4))
    assert buffer.is_empty
    assert state.cleanup_sessions([survivor]) == 0


@pytest.mark.parametrize('sources', [('mic', 'mic'), ('file', 'file'), ('mic', 'file')])
@pytest.mark.parametrize('first', ['a', 'b'])
def test_audio_to_final_delivery_stays_with_its_connection(handler, sources, first):
    """Exercise receive, scheduling, real merging, final cleanup and wire delivery."""
    source_by_socket = dict(zip(('a', 'b'), sources))
    words = {'a': ['甲', '乙'], 'b': ['丙', '丁']}
    amplitudes = {'a': 0.25, 'b': 0.5}
    received_samples = []

    def create_stream():
        stream = SimpleNamespace()

        def accept_waveform(samplerate, samples):
            assert samplerate == 16000
            stream.samples = samples
            received_samples.append(samples.copy())

        stream.accept_waveform = accept_waveform
        return stream

    counts = {'a': 0, 'b': 0}

    def decode_stream(stream, **kwargs):
        socket = 'a' if np.max(stream.samples) < 0.4 else 'b'
        text = words[socket][counts[socket]]
        counts[socket] += 1
        stream.result = SimpleNamespace(text=text, tokens=[text], timestamps=[0.0])

    recognizer = SimpleNamespace(
        create_stream=create_stream, decode_stream=decode_stream,
        capabilities={EngineCapabilities.TIMESTAMPS},
    )
    handler.set_engine(recognizer)
    handler.pipeline.formatter = SimpleNamespace(format=lambda text, **kwargs: text)
    sockets = {
        socket: SimpleNamespace(id=socket, send=AsyncMock()) for socket in ('a', 'b')
    }
    app = SimpleNamespace(state=SimpleNamespace(
        queue_in=handler.queue_in, queue_out=queue.Queue(),
        sockets=sockets, socket_last_activity={},
    ))
    other = 'b' if first == 'a' else 'a'
    order = (first, other)
    audio = {
        socket: (amplitude * np.sin(2 * np.pi * 400 * np.arange(16000) / 16000))
        .astype(np.float32).tobytes()
        for socket, amplitude in amplitudes.items()
    }

    async def receive():
        caches = {}
        for final in (False, True):
            for socket in order:
                msg = AudioMessage.from_dict(json.loads(AudioMessage(
                    task_id='shared', source=source_by_socket[socket],
                    data=base64.b64encode(audio[socket]).decode('ascii'),
                    is_final=final, time_start=0.0, seg_duration=1.0, seg_overlap=0.0,
                ).to_json()))
                cache = caches.setdefault(socket, AudioCache(msg))
                await message_handler(sockets[socket], msg, cache, app)

    asyncio.run(receive())
    assert handler.drain_queue()
    expected_audio = []
    for final in (False, True):
        for socket in order:
            task = handler.buffer.pop()
            assert (task.socket_id, task.task_id, task.is_final) == (socket, 'shared', final)
            expected_audio.append(np.frombuffer(audio[socket], dtype=np.float32))
            handler.handle_audio_task(task)
            result = handler.queue_out.get_nowait()
            assert (result.socket_id, result.task_id, result.type) == (
                socket, 'shared', source_by_socket[socket],
            )
            assert result.is_final is final
            assert result.duration == pytest.approx(2.0 if final else 1.0)
            assert result.tokens == words[socket][:(2 if final else 1)]
            assert result.timestamps == ([0.0, 1.0] if final else [0.0])
            # Freeze the queue payload as multiprocessing serialization would.
            app.state.queue_out.put(pickle.loads(pickle.dumps(result)))
            if final:
                assert task.key not in handler.state.sessions
                if socket == first:
                    assert (other, 'shared') in handler.state.sessions
            handler.cleanup()

    np.testing.assert_array_equal(received_samples, expected_audio)
    assert not handler.state.sessions
    assert handler.buffer.is_empty
    app.state.queue_out.put(None)
    asyncio.run(asyncio.wait_for(ws_send(app), timeout=5))
    for socket in order:
        messages = [json.loads(call.args[0]) for call in sockets[socket].send.await_args_list]
        assert len(messages) == 2
        assert [msg['task_id'] for msg in messages] == ['shared', 'shared']
        assert [msg['is_final'] for msg in messages] == [False, True]
        assert [msg['text'] for msg in messages] == [words[socket][0], ''.join(words[socket])]
        assert messages[-1]['text_accu'] == ''.join(words[socket])
        assert all('socket_id' not in msg for msg in messages)


def test_idle_poll_cleans_up_disconnected_sessions(handler):
    handler.state.get_session('shared', 'a', 'mic')
    handler.state.get_session('shared', 'b', 'mic')
    handler.sockets_id.remove('a')
    handler.queue_in = Mock()
    handler.queue_in.get.side_effect = [queue.Empty, None]
    handler.cleanup_engines = Mock()

    assert handler.drain_queue() is False
    assert set(handler.state.sessions) == {('b', 'shared')}


def test_disconnect_after_drain_skips_stale_buffer_before_inference(handler):
    handler.buffer.enqueue(make_task('a'))
    survivor = make_task('b', final=True)
    handler.buffer.enqueue(survivor)
    handler.sockets_id.remove('a')
    handler.drain_queue = Mock(side_effect=[True, False])
    def process_survivor(task):
        assert set(handler.state.sessions) == {('b', 'shared')}
    handler.handle_audio_task = Mock(side_effect=process_survivor)

    handler.loop()

    handler.handle_audio_task.assert_called_once_with(survivor)
    assert not handler.state.sessions
    handler.gpu_monitor.close.assert_called_once()


def test_disconnect_during_inference_drops_result_without_removing_other_session(handler):
    task = make_task('a', final=True)
    session = handler.state.get_session('shared', 'a', 'mic')
    other = handler.state.get_session('shared', 'b', 'mic')

    def process(_task):
        handler.sockets_id.remove('a')
        session.result.is_final = True
        return session.result

    handler.pipeline = SimpleNamespace(process=process)
    handler.handle_audio_task(task)
    handler.cleanup()

    assert handler.queue_out.empty()
    assert handler.state.sessions == {('b', 'shared'): other}


def test_queued_result_for_disconnected_socket_is_not_sent_to_same_id_peer():
    state = WorkerState()
    stale = state.get_session('shared', 'a', 'mic').result
    live = state.get_session('shared', 'b', 'mic').result
    stale.text, live.text = 'alpha', 'bravo'
    out = queue.Queue()
    for item in (stale, live, None):
        out.put(item)
    socket = SimpleNamespace(id='b', send=AsyncMock())
    app = SimpleNamespace(state=SimpleNamespace(
        queue_out=out, sockets={'b': socket}, socket_last_activity={},
    ))

    async def run():
        await asyncio.wait_for(ws_send(app), timeout=5)

    asyncio.run(run())

    socket.send.assert_awaited_once()
    assert json.loads(socket.send.await_args.args[0])['text'] == 'bravo'
