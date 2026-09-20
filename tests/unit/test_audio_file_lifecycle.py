"""File I/O and encoder failures using synthetic samples and fake child processes."""

import asyncio
from pathlib import Path
from subprocess import TimeoutExpired
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from core.client.audio.file_manager import AudioFileManager
from core.client.audio.file_writer import AsyncAudioWriter


class Encoder:
    def __init__(self, *args, **kwargs):
        self.returncode = None
        self.payload = bytearray()
        self.stdin = SimpleNamespace(write=self.write, close=Mock())
        self.kills = 0
        self.waits = []
        self.block = False
        self.hang_on_finish = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, data):
        self.entered.set()
        if self.block:
            assert self.release.wait(3)
        if self.returncode is not None:
            raise BrokenPipeError('synthetic')
        count = min(len(data), 17)
        self.payload.extend(data[:count])
        return count

    def poll(self):
        return self.returncode

    def kill(self):
        self.kills += 1
        self.returncode = -1
        self.release.set()

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.hang_on_finish and self.returncode is None:
            raise TimeoutExpired('synthetic-encoder', timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr('core.client.audio.file_manager.Popen', Encoder)
    monkeypatch.setattr('core.client.audio.file_manager.shutil.which', lambda _: None)
    manager = AudioFileManager()
    manager.file_handle = Encoder()
    manager.file_path = Path('synthetic.mp3')
    return manager


def test_partial_pipe_writes_preserve_all_audio_bytes(manager):
    data = np.arange(240, dtype=np.float32).reshape(-1, 1) / 240
    encoder = manager.file_handle
    manager.write(data)
    assert bytes(encoder.payload) == data.tobytes()
    assert manager.finish() == Path('synthetic.mp3')
    assert encoder.waits == [manager.FINISH_TIMEOUT]
    assert manager.file_handle is None
    manager.finish()
    encoder.stdin.close.assert_called_once()


def test_finish_timeout_kills_and_reaps_encoder(manager):
    encoder = manager.file_handle
    encoder.hang_on_finish = True
    with pytest.raises(RuntimeError, match='AudioEncoderFinishFailed'):
        manager.finish()
    assert encoder.kills == 1
    assert encoder.waits == [manager.FINISH_TIMEOUT, manager.KILL_TIMEOUT]
    assert manager.file_handle is None


def test_nonzero_encoder_exit_is_not_reported_as_success(manager):
    manager.file_handle.returncode = 1
    with pytest.raises(RuntimeError, match='AudioEncoderFailed'):
        manager.finish()


def test_cancel_interrupts_blocked_write_and_keeps_loop_responsive(manager):
    async def run():
        writer = AsyncAudioWriter(manager)
        encoder = manager.file_handle
        encoder.block = True
        operation = asyncio.create_task(writer.call(manager.write, np.zeros((240, 1), np.float32)))
        assert await asyncio.to_thread(encoder.entered.wait, 2)
        # Reaching this line proves the event loop did not execute the pipe write.
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 2)
        await writer.close(abort=True)
        assert encoder.kills == 1
        assert encoder.waits == [manager.FINISH_TIMEOUT]
        assert manager.file_handle is None

    asyncio.run(run())


def test_write_deadline_aborts_and_reaps_encoder(manager):
    async def run():
        writer = AsyncAudioWriter(manager)
        writer.IO_TIMEOUT = 0.05
        encoder = manager.file_handle
        encoder.block = True
        with pytest.raises(asyncio.TimeoutError):
            await writer.call(manager.write, np.zeros((240, 1), np.float32))
        await writer.close(abort=True)
        assert encoder.kills == 1
        assert manager.file_handle is None

    asyncio.run(run())


def test_create_completing_after_abort_closes_its_late_child(manager, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    manager.file_handle = None
    manager._ffmpeg_path = 'synthetic-ffmpeg'
    entered, release = threading.Event(), threading.Event()
    created = []

    class SlowEncoder(Encoder):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)
            entered.set()
            assert release.wait(2)

    monkeypatch.setattr('core.client.audio.file_manager.Popen', SlowEncoder)
    errors = []

    def create():
        try:
            manager.create(1, 0)
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=create)
    thread.start()
    assert entered.wait(2)
    manager.abort()
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert errors == ['AudioWriterAborted']
    assert created[0].kills == 1
    assert created[0].waits == [manager.FINISH_TIMEOUT]
    assert manager.file_handle is None


def test_repeated_cancellation_does_not_skip_writer_cleanup():
    async def run():
        entered, release = threading.Event(), threading.Event()

        def finish():
            entered.set()
            assert release.wait(2)

        manager = SimpleNamespace(finish=Mock(side_effect=finish), abort=Mock())
        writer = AsyncAudioWriter(manager)
        operation = asyncio.create_task(writer.close())
        assert await asyncio.to_thread(entered.wait, 2)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 2)
        manager.finish.assert_called_once()
        assert writer._closed

    asyncio.run(run())


def test_wav_fallback_writes_a_complete_synthetic_file(monkeypatch, tmp_path):
    import wave
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('core.client.audio.file_manager.shutil.which', lambda _: None)

    async def run():
        manager = AudioFileManager()
        writer = AsyncAudioWriter(manager)
        path, _ = await writer.call(manager.create, 1, 0)
        await writer.call(manager.write, np.zeros((2400, 1), dtype=np.float32))
        await writer.close()
        with wave.open(str(path), 'rb') as audio:
            assert audio.getframerate() == 48000
            assert audio.getnframes() == 2400
            assert audio.getnchannels() == 1
        assert manager.file_handle is None

    asyncio.run(run())
