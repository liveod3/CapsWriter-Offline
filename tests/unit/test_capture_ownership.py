"""Recording ownership regressions with synthetic audio and no hardware/UI."""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from core.client.audio.capture import CaptureSession
from core.client.audio.recorder import AudioRecorder
from core.client.audio.stream import AudioStreamManager
from core.client.shortcut.task import ShortcutTask
from core.client.state import ClientState


@pytest.fixture(autouse=True)
def no_hardware_or_ui(monkeypatch):
    for name in ('show_status_hint', 'hide_status_hint', 'show_recording_indicator',
                 'hide_recording_indicator', 'set_recording_state', 'Status', 'Thread'):
        monkeypatch.setattr(f'core.client.shortcut.task.{name}', Mock())
    monkeypatch.setattr('core.client.caret_context.foreground_window', lambda: 42)
    monkeypatch.setattr('core.client.audio.recorder.Config.save_audio', False)
    monkeypatch.setattr('core.client.audio.recorder.Config.threshold', 0.0)


def make_app(loop):
    return SimpleNamespace(
        loop=loop, state=ClientState(), _stopping=False,
        mark_user_activity=Mock(), progress=Mock(),
        stream=SimpleNamespace(get_ready_event=lambda: Event(), is_ready=lambda _: True),
        caret_context=SimpleNamespace(capture=AsyncMock(return_value='')),
        ws=SimpleNamespace(is_connected=True, send=AsyncMock(return_value=True)),
    )


def make_task(app, key='ctrl_r', recorder_class=AudioRecorder):
    return ShortcutTask(app, SimpleNamespace(
        key=key, is_toggle_key=lambda: False, suppress=False,
    ), recorder_class=recorder_class)


async def settle(future):
    try:
        await asyncio.wait_for(asyncio.wrap_future(future), timeout=2)
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0)


def test_two_shortcuts_racing_for_capture_have_one_owner():
    async def run():
        app = make_app(asyncio.get_running_loop())
        tasks = [make_task(app, 'ctrl_r'), make_task(app, 'x2')]
        barrier = Barrier(2)

        def launch(task):
            barrier.wait(timeout=2)
            return task.launch()

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(launch, task) for task in tasks]
            results = [attempt.result(timeout=2) for attempt in attempts]
        assert sorted(results) == [False, True]
        owner = tasks[results.index(True)]
        peer = tasks[results.index(False)]
        assert app.state.recording_owner is owner
        assert owner.is_recording and not peer.is_recording
        assert owner.launch() is False
        peer.cancel()
        peer.finish()
        assert app.state.recording_owner is owner
        future = owner.task
        owner.cancel()
        await settle(future)
        assert not app.state.recording
        assert app.state.recording_owner is None

    asyncio.run(run())


@pytest.mark.parametrize('same_shortcut', [False, True])
def test_finished_recorder_drains_private_audio_while_new_capture_runs(same_shortcut):
    async def run():
        app = make_app(asyncio.get_running_loop())
        first = make_task(app)
        second = first if same_shortcut else make_task(app, 'x2')
        old_send_started = asyncio.Event()
        release_old_send = asyncio.Event()
        sent = []

        async def send(message):
            if message.task_id == first_id and not message.is_final:
                old_send_started.set()
                await release_old_send.wait()
            sent.append(message)
            return True

        app.ws.send = send
        assert first.launch()
        first_id = first._progress_id
        first_capture, first_future = first._capture, first.task
        first_capture.push_audio(np.full((2400, 1), 0.25, dtype=np.float32),
                                 first.recording_start_time + 1)
        await asyncio.wait_for(old_send_started.wait(), 2)
        first.finish()
        first.finish()
        assert second.launch()
        second_id, second_future = second._progress_id, second.task
        assert first_id != second_id
        assert not first_capture.push_audio(np.zeros((2400, 1), dtype=np.float32), 101.0)
        second._capture.push_audio(np.full((2400, 1), 0.5, dtype=np.float32),
                                   second.recording_start_time + 1)
        release_old_send.set()
        await settle(first_future)
        assert second.is_recording
        assert app.state.recording_owner is second
        second.finish()
        await settle(second_future)
        assert not app.state.recording
        assert not first._pending_recorders and not second._pending_recorders
        for task_id, value in ((first_id, 0.25), (second_id, 0.5)):
            messages = [msg for msg in sent if msg.task_id == task_id]
            assert [msg.is_final for msg in messages] == [False, True]
            data = np.frombuffer(base64.b64decode(messages[0].data), dtype=np.float32)
            np.testing.assert_array_equal(data, np.full(800, value, dtype=np.float32))

    asyncio.run(run())


def test_cancel_then_immediate_relaunch_rejects_old_data_and_completion():
    async def run():
        app = make_app(asyncio.get_running_loop())
        task = make_task(app)
        assert task.launch()
        old_capture, old_future = task._capture, task.task
        old_capture.push_audio(np.ones((2400, 1), dtype=np.float32), 100.0)
        task.cancel()
        assert task.launch()
        new_future = task.task
        await settle(old_future)
        assert task.is_recording and app.state.capture is task._capture
        assert not old_capture.push_audio(np.ones((2400, 1), dtype=np.float32), 101.0)
        task.finish()
        await settle(new_future)
        assert all(not call.args[0].data for call in app.ws.send.await_args_list)

    asyncio.run(run())


def test_readiness_timeout_releases_capture_and_allows_retry():
    async def run():
        app = make_app(asyncio.get_running_loop())
        app.stream.is_ready = lambda _: False
        task = make_task(app)
        task.AUDIO_READY_TIMEOUT = 0
        assert task.launch()
        future = task.task
        generation = task._launch_generation
        task._wait_for_audio_ready(Event(), generation)
        await settle(future)
        assert not task.is_recording and not app.state.recording
        assert app.state.recording_owner is None
        assert app.state.capture is None
        assert not app.state.task_contexts
        assert task.launch()
        retry = task.task
        task._wait_for_audio_ready(Event(), generation)
        assert task.is_recording
        task.cancel()
        await settle(retry)

    asyncio.run(run())


def test_recorder_failure_releases_owner(monkeypatch):
    class FailingRecorder:
        task_id = 'failure'

        def __init__(self, app):
            pass

        async def record_and_send(self, capture):
            raise ValueError('synthetic')

    async def run():
        app = make_app(asyncio.get_running_loop())
        task = make_task(app, recorder_class=FailingRecorder)
        assert task.launch()
        future = task.task
        with pytest.raises(ValueError):
            await settle(future)
        assert not task.is_recording and not app.state.recording
        assert app.state.capture is None
        app.progress.finish.assert_called_with('failure')

    asyncio.run(run())


def test_submission_failure_rolls_back_capture(monkeypatch):
    async def run():
        app = make_app(asyncio.get_running_loop())
        task = make_task(app)
        monkeypatch.setattr('core.client.shortcut.task.asyncio.run_coroutine_threadsafe',
                            Mock(side_effect=RuntimeError('closed loop')))
        assert task.launch() is False
        assert not task.is_recording and not app.state.recording
        assert app.state.recording_owner is None

    asyncio.run(run())


def test_failed_relaunch_does_not_cancel_previous_tail(monkeypatch):
    async def run():
        app = make_app(asyncio.get_running_loop())
        task = make_task(app)
        assert task.launch()
        previous = task.task
        task.finish()
        monkeypatch.setattr('core.client.shortcut.task.asyncio.run_coroutine_threadsafe',
                            Mock(side_effect=RuntimeError('closed loop')))
        assert task.launch() is False
        assert not previous.cancelled()
        await settle(previous)
        assert not app.state.recording
        assert not app.state.recording_futures

    asyncio.run(run())


@pytest.mark.parametrize('paused,stopping', [(True, False), (False, True)])
def test_manual_pause_and_shutdown_reject_launch(paused, stopping):
    async def run():
        app = make_app(asyncio.get_running_loop())
        app._stopping = stopping
        app.state.dictation_manually_paused = paused
        assert make_task(app).launch() is False
        assert app.state.capture is None

    asyncio.run(run())


def test_close_cancels_both_draining_and_current_recorder():
    async def run():
        app = make_app(asyncio.get_running_loop())
        gate = asyncio.Event()

        async def wait_for_context(_, **kwargs):
            await gate.wait()
            return ''

        app.caret_context.capture = wait_for_context
        task = make_task(app)
        assert task.launch()
        old = task.task
        task.finish()
        assert task.launch()
        current = task.task
        task.close()
        await settle(old)
        await settle(current)
        assert old.cancelled() and current.cancelled()
        assert not task._pending_recorders
        assert not app.state.recording

    asyncio.run(run())


def test_capture_buffer_is_bounded_and_coalesces_loop_notifications():
    loop = Mock()
    capture = CaptureSession(loop, 0, 0, max_blocks=2)
    block = np.zeros((2400, 1), dtype=np.float32)
    assert capture.push_audio(block, 1)
    assert capture.push_audio(block, 2)
    for _ in range(100):
        assert not capture.push_audio(block, 3)
    assert capture._audio_blocks == 0
    assert len(capture._events) == 1
    assert asyncio.run(capture.get()) == {'type': 'overflow'}
    loop.call_soon_threadsafe.assert_called_once()


def test_capture_finish_has_reserved_capacity_and_preserves_fifo():
    capture = CaptureSession(Mock(), 0, 42, max_blocks=2)
    block = np.zeros((2400, 1), dtype=np.float32)
    capture.push_audio(block, 1)
    capture.push_audio(block, 2)
    capture.finish()
    capture.finish()

    async def run():
        events = [await capture.get() for _ in range(4)]
        assert [event['type'] for event in events] == ['begin', 'data', 'data', 'finish']
        assert [event['time'] for event in events[1:3]] == [1, 2]

    asyncio.run(run())


def test_capture_consumer_wakes_for_thread_producer_and_cancel():
    async def run():
        capture = CaptureSession(asyncio.get_running_loop(), 0, 0)
        assert (await capture.get())['type'] == 'begin'
        consumer = asyncio.create_task(capture.get())
        await asyncio.sleep(0)
        await asyncio.to_thread(capture.push_audio, np.zeros((2400, 1), dtype=np.float32), 1)
        assert (await asyncio.wait_for(consumer, 2))['type'] == 'data'
        consumer = asyncio.create_task(capture.get())
        await asyncio.sleep(0)
        await asyncio.to_thread(capture.cancel)
        assert (await asyncio.wait_for(consumer, 2))['type'] == 'cancel'

    asyncio.run(run())


def test_closed_loop_callback_is_safely_dropped():
    loop = Mock()
    loop.call_soon_threadsafe.side_effect = RuntimeError('closed')
    capture = CaptureSession(loop, 0, 0)
    capture.push_audio(np.zeros((2400, 1), dtype=np.float32), 0)
    assert capture._closed
    assert not capture._events


def test_stale_stream_callback_cannot_mark_ready_or_deliver_audio():
    app = SimpleNamespace(state=ClientState(), loop=Mock())
    manager = AudioStreamManager(app)
    app.state.recording = True
    app.state.capture = Mock()
    old = manager.get_ready_event()
    manager.stop()
    manager._audio_callback(np.zeros((2400, 1), dtype=np.float32), 2400, None, None, old)
    assert not old.is_set()
    app.state.capture.push_audio.assert_not_called()


def test_callback_keeps_capture_snapshot_when_recording_changes_mid_callback():
    app = SimpleNamespace(state=ClientState(), loop=Mock())
    manager = AudioStreamManager(app)
    old_capture = CaptureSession(app.loop, 0, 0)
    new_capture = CaptureSession(app.loop, 1, 0)
    app.state.recording = True
    app.state.capture = old_capture

    def switch_capture():
        old_capture.finish()
        app.state.capture = new_capture

    manager._ready_event = Mock()
    manager._ready_event.is_set.return_value = False
    manager._ready_event.set.side_effect = switch_capture
    manager._audio_callback(np.zeros((2400, 1), dtype=np.float32), 2400, None, None)
    assert old_capture._audio_blocks == new_capture._audio_blocks == 0


def test_overflow_releases_shortcut_state_and_reports_failure(monkeypatch):
    async def run():
        app = make_app(asyncio.get_running_loop())
        task = make_task(app)
        assert task.launch()
        future = task.task
        task._capture._max_blocks = 1
        task._capture.push_audio(np.zeros((2400, 1), dtype=np.float32), 1)
        task._capture.push_audio(np.zeros((2400, 1), dtype=np.float32), 2)
        with pytest.raises(RuntimeError, match='CaptureBufferOverflow'):
            await settle(future)
        assert not app.state.recording and app.state.capture is None
        assert not app.state.recording_futures
        assert not app.state.task_contexts
        from core.client.shortcut import task as module
        assert 'buffer is full' in module.show_status_hint.call_args.args[0]
        app.ws.send.assert_not_awaited()

    asyncio.run(run())


def test_repeated_finish_and_relaunch_cannot_accumulate_unlimited_recorders():
    async def run():
        app = make_app(asyncio.get_running_loop())
        task = make_task(app)
        task.MAX_PENDING_RECORDERS = 2
        futures = []
        for _ in range(2):
            assert task.launch()
            futures.append(task.task)
            task.finish()
        assert task.launch() is False
        assert len(app.state.recording_futures) == 2
        task.close()
        for future in futures:
            await settle(future)
        assert not app.state.recording_futures
        assert task.launch()
        future = task.task
        task.close()
        await settle(future)

    asyncio.run(run())


def test_recorder_closes_audio_writer_when_write_fails(monkeypatch):
    writer = Mock()
    writer.create.return_value = ('synthetic.wav', None)
    writer.write.side_effect = OSError('synthetic write failure')
    monkeypatch.setattr('core.client.audio.recorder.Config.save_audio', True)
    monkeypatch.setattr('core.client.audio.recorder.AudioFileManager', lambda: writer)

    async def run():
        app = make_app(asyncio.get_running_loop())
        capture = CaptureSession(app.loop, 0, 0)
        capture.push_audio(np.zeros((2400, 1), dtype=np.float32), 1)
        recorder = AudioRecorder(app)
        with pytest.raises(OSError):
            await recorder.record_and_send(capture)
        writer.finish.assert_called_once()
        assert not app.state.task_contexts
        assert not recorder._cache

    asyncio.run(run())
