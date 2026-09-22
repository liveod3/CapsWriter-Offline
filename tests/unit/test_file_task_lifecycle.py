"""Bounded file-task outcomes with synthetic audio and owned fake processes."""

import asyncio
import io
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

from core.client.manager.file_runner import FileRunner
from core.client.transcribe.file_transcriber import FileTranscriber
from core.client.transcribe.file_transcriber import TranscriptionSummary
from core.client.transcribe.media_tool import MediaTool
from core.client.transcribe import lifecycle
from core.protocol import RecognitionMessage
from core.logger import ConsoleFeedbackFilter


class FakeProcess:
    def __init__(self, data=b'', *, blocked=False, stubborn=False, exit_code=0):
        self.returncode = None
        self.data = data
        self.blocked = blocked
        self.stubborn = stubborn
        self.exit_code = exit_code
        self.reading = asyncio.Event()
        self.exited = asyncio.Event()
        self.terminated = 0
        self.killed = 0
        self.reaped = False
        self.stdout = self

    async def read(self, size):
        self.reading.set()
        if self.blocked:
            await asyncio.Event().wait()
        data, self.data = self.data[:size], self.data[size:]
        if not data:
            self.returncode = self.exit_code
            self.exited.set()
        return data

    async def wait(self):
        await self.exited.wait()
        self.reaped = True
        return self.returncode

    async def communicate(self):
        await self.exited.wait()
        return b'', b''

    def terminate(self):
        self.terminated += 1
        if not self.stubborn:
            self.returncode = -1
            self.exited.set()

    def kill(self):
        self.killed += 1
        self.returncode = -9
        self.exited.set()


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setattr('core.client.transcribe.file_transcriber.Config.file_seg_duration', 0.1)
    monkeypatch.setattr('core.client.transcribe.file_transcriber.Config.file_seg_overlap', 0)
    monkeypatch.setattr(MediaTool, 'get_audio_duration', AsyncMock(return_value=0))
    monkeypatch.setattr(FileTranscriber, 'check', AsyncMock(return_value=True))
    monkeypatch.setattr(FileTranscriber, '_start_progress', Mock())
    save = Mock(return_value=('synthetic', 1, [Path('synthetic.txt')]))
    monkeypatch.setattr('core.client.transcribe.file_transcriber.ResultHandler.save_results', save)
    monkeypatch.setattr(lifecycle, 'PROCESS_GRACE_SECONDS', 0.01)
    monkeypatch.setattr(lifecycle, 'PROCESS_KILL_SECONDS', 0.1)

    def make(process):
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        ws = SimpleNamespace(send=AsyncMock(return_value=True), receive=AsyncMock(), close=AsyncMock())
        app = SimpleNamespace(ws=ws, state=SimpleNamespace(websocket=None))
        transcriber = FileTranscriber(app, Path('synthetic.wav'), output_formats=frozenset({'txt'}))
        transcriber._io_timeout = 0.1
        transcriber._result_timeout = 0.1
        monkeypatch.setattr('core.client.transcribe.FileTranscriber', lambda *a, **k: transcriber)
        runner = FileRunner(app, [], output_formats=frozenset({'txt'}))
        return transcriber, runner, ws, save

    return make


def result(task_id, duration=0.1, *, final=False):
    return RecognitionMessage(task_id, final, duration, 0, 0, 0, 'synthetic')


def test_normal_file_waits_for_sender_and_saves_only_matching_final(factory):
    async def run():
        process = FakeProcess(b'\0' * 6400)
        transcriber, runner, ws, save = factory(process)

        async def receive():
            await transcriber._send_complete.wait()
            return result(transcriber.task_id, final=True)

        ws.receive.side_effect = receive
        summary = await runner._process_file(Path('synthetic.wav'))
        assert summary is transcriber.summary and summary is not None
        assert process.reaped
        assert process.terminated == 0
        assert all(call.args[0].supports_task_errors for call in ws.send.await_args_list)
        save.assert_called_once()
        ws.close.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize('failure', ['send', 'decode', 'read_timeout', 'send_timeout'])
def test_sender_failure_cancels_receiver_and_reaps_decoder(factory, failure):
    async def run():
        process = FakeProcess(b'\0' * 6400, blocked=failure == 'read_timeout', exit_code=1)
        transcriber, runner, ws, save = factory(process)
        if failure == 'send':
            ws.send.return_value = False
        if failure == 'send_timeout':
            async def stalled_send(_message):
                await asyncio.Event().wait()

            ws.send.side_effect = stalled_send
        receiver_closed = asyncio.Event()

        async def receive():
            try:
                await asyncio.Event().wait()
            finally:
                receiver_closed.set()

        ws.receive.side_effect = receive
        assert await runner._process_file(Path('synthetic.wav')) is None
        assert receiver_closed.is_set()
        assert process.reaped
        save.assert_not_called()
        ws.close.assert_awaited_once()

    asyncio.run(run())


def test_disconnect_cancels_blocked_decode_and_escalates_stubborn_child(factory):
    async def run():
        process = FakeProcess(blocked=True, stubborn=True)
        _, runner, ws, save = factory(process)

        async def receive():
            await process.reading.wait()
            raise ConnectionError('synthetic')

        ws.receive.side_effect = receive
        assert await runner._process_file(Path('synthetic.wav')) is None
        assert process.terminated == process.killed == 1
        assert process.reaped
        save.assert_not_called()

    asyncio.run(run())


def test_server_task_error_cancels_upload_and_reaps_decoder_before_return(factory):
    async def run():
        process = FakeProcess(blocked=True)
        transcriber, runner, ws, save = factory(process)

        async def receive():
            await process.reading.wait()
            return RecognitionMessage(transcriber.task_id, True, 0, 0, 0, 0, '',
                                      error_code='recognition_failed')

        ws.receive.side_effect = receive
        assert await runner._process_file(Path('synthetic.wav')) is None
        assert transcriber.failure_code == 'recognition_failed'
        assert not transcriber._send_complete.is_set()
        assert process.terminated == 1 and process.reaped
        save.assert_not_called()
        ws.close.assert_awaited_once()
        assert not [task for task in asyncio.all_tasks()
                    if task is not asyncio.current_task() and not task.done()]

    asyncio.run(run())


def test_receiver_timeout_after_upload_completes_is_failure(factory):
    async def run():
        process = FakeProcess(b'\0' * 6400)
        transcriber, runner, ws, save = factory(process)
        ws.receive.side_effect = asyncio.Event().wait
        assert await runner._process_file(Path('synthetic.wav')) is None
        assert transcriber._send_complete.is_set()
        assert process.reaped
        save.assert_not_called()

    asyncio.run(run())


def test_exhausted_send_window_has_a_deadline_and_reaps_decoder(factory):
    async def run():
        process = FakeProcess(b'\0' * 6400)
        transcriber, _, ws, _ = factory(process)
        transcriber._send_window = asyncio.Semaphore(0)
        transcriber._result_timeout = 0.02
        assert await asyncio.wait_for(transcriber.send(), 1) is False
        ws.send.assert_not_awaited()
        assert process.reaped

    asyncio.run(run())


def test_final_before_failed_send_cannot_create_outputs(factory):
    async def run():
        process = FakeProcess(b'\0' * 6400)
        transcriber, runner, ws, save = factory(process)
        ws.send.return_value = False
        ws.receive.return_value = result(transcriber.task_id, final=True)
        assert await runner._process_file(Path('synthetic.wav')) is None
        save.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize('kind', ['foreign', 'duplicate'])
def test_stale_results_do_not_refresh_deadline_or_send_window(factory, kind):
    async def run():
        transcriber, _, ws, save = factory(FakeProcess())
        transcriber._result_timeout = 0.02
        transcriber._send_complete.set()
        transcriber._send_window = Mock()
        if kind == 'foreign':
            ws.receive.return_value = result('another-task', final=True)
        else:
            ws.receive.return_value = result(transcriber.task_id, duration=0)
        assert await asyncio.wait_for(transcriber.receive(), 1) is False
        transcriber._send_window.release.assert_not_called()
        save.assert_not_called()

    asyncio.run(run())


def test_matching_progress_extends_stall_deadline(factory):
    async def run():
        transcriber, _, ws, save = factory(FakeProcess())
        transcriber._result_timeout = 0.1
        transcriber._send_complete.set()
        messages = iter([result(transcriber.task_id, value, final=value == 4)
                         for value in (1, 2, 3, 4)])

        async def receive():
            await asyncio.sleep(0.04)
            return next(messages)

        ws.receive.side_effect = receive
        assert await transcriber.receive() is True
        save.assert_called_once()

    asyncio.run(run())


def test_repeated_parent_cancellation_waits_for_both_children_and_close(factory):
    async def run():
        process = FakeProcess(blocked=True)
        _, runner, ws, save = factory(process)
        ws.receive.side_effect = asyncio.Event().wait
        closing, allow_close = asyncio.Event(), asyncio.Event()

        async def close():
            closing.set()
            await allow_close.wait()

        ws.close.side_effect = close
        operation = asyncio.create_task(runner._process_file(Path('synthetic.wav')))
        await process.reading.wait()
        operation.cancel()
        await asyncio.wait_for(closing.wait(), 1)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        assert process.reaped
        allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        ws.close.assert_awaited_once()
        save.assert_not_called()

    asyncio.run(run())


def test_cancel_during_process_creation_reaps_late_child(monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        process = FakeProcess()

        async def create(*args, **kwargs):
            entered.set()
            await release.wait()
            return process

        monkeypatch.setattr(asyncio, 'create_subprocess_exec', create)
        operation = asyncio.create_task(lifecycle.open_process('synthetic'))
        await entered.wait()
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 1)
        assert process.reaped and process.terminated == 1

    asyncio.run(run())


def test_reap_real_python_child_with_buffered_stdout():
    async def run():
        process = await lifecycle.open_process(
            sys.executable, '-c',
            'import sys,time; sys.stdout.buffer.write(b"x" * 1000000); '
            'sys.stdout.buffer.flush(); time.sleep(60)',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            assert await asyncio.wait_for(process.stdout.read(1), 5) == b'x'
        finally:
            await lifecycle.complete_cleanup(lifecycle.reap_process(process))
        assert process.returncode is not None
        assert process.stdout.at_eof()

    asyncio.run(run())


@pytest.mark.parametrize('cancel', [False, True])
def test_probe_timeout_or_cancel_reaps_child(monkeypatch, cancel):
    async def run():
        process = FakeProcess(stubborn=True)
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        monkeypatch.setattr('core.client.transcribe.media_tool.PROBE_TIMEOUT_SECONDS', 0.02)
        monkeypatch.setattr(lifecycle, 'PROCESS_GRACE_SECONDS', 0.01)
        operation = asyncio.create_task(MediaTool.get_audio_duration(Path('synthetic.wav')))
        if cancel:
            await asyncio.sleep(0.01)
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            assert await operation == 0
        assert process.reaped and process.killed == 1

    asyncio.run(run())


@pytest.mark.parametrize('value', [None, True, 0, -1, 'bad', float('inf'), float('nan')])
def test_invalid_timeout_uses_compatible_default(value):
    assert lifecycle.positive_timeout(SimpleNamespace(timeout=value), 'timeout', 60) == 60


def test_connection_close_failure_aborts_only_owned_socket(factory):
    async def run():
        transcriber, _, ws, _ = factory(FakeProcess())
        socket = SimpleNamespace(transport=Mock())
        transcriber.state.websocket = socket
        ws.close.side_effect = OSError('synthetic')
        await transcriber.close()
        socket.transport.abort.assert_called_once()
        assert transcriber.state.websocket is None

    asyncio.run(run())


def test_batch_continues_only_after_failed_file_cleanup(monkeypatch):
    events = []
    summary = TranscriptionSummary(1, 1, 1, (), 1)

    class Transcriber:
        def __init__(self, _app, file, **_kwargs):
            self.file = file
            self.summary = summary
            self.receiving = asyncio.Event()

        async def check(self):
            if self.file.name == 'good.wav':
                assert events == ['receiver stopped', 'closed bad.wav']
            return True

        async def send(self):
            await self.receiving.wait()
            return self.file.name == 'good.wav'

        async def receive(self):
            self.receiving.set()
            if self.file.name == 'good.wav':
                return True
            try:
                await asyncio.Event().wait()
            finally:
                events.append('receiver stopped')

        async def close(self):
            events.append(f'closed {self.file.name}')

    monkeypatch.setattr('core.client.transcribe.FileTranscriber', Transcriber)
    monkeypatch.setattr('core.client.manager.file_runner.Config.file_separate_log', False)
    monkeypatch.setattr('core.client.manager.file_runner.sys.stdin', SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr('core.client.ui.TipsDisplay.show_file_tips', Mock())
    app = SimpleNamespace(state=SimpleNamespace(), ws=SimpleNamespace())
    runner = FileRunner(app, [Path('bad.wav'), Path('good.wav')], output_formats=frozenset({'txt'}))
    assert asyncio.run(runner.run()) is False
    assert events == ['receiver stopped', 'closed bad.wav', 'closed good.wav']


@pytest.mark.parametrize('language', ['en', 'zh-CN'])
def test_invalid_media_has_one_readable_failure_and_retains_diagnostics(factory, monkeypatch, language):
    from core.i18n import set_language, tr
    set_language(language)
    async def run():
        process = FakeProcess(exit_code=1)
        transcriber, runner, ws, _ = factory(process)
        runner.files = [Path('synthetic.wav')]
        ws.receive.side_effect = asyncio.Event().wait
        terminal, diagnostics = io.StringIO(), io.StringIO()
        styles = {f'ui.{name}': '' for name in (
            'error', 'label', 'value', 'muted', 'secondary', 'title', 'success',
            'accent', 'border')}
        console = Console(file=terminal, width=100, theme=Theme(styles))
        handler = RichHandler(console=console, show_path=False)
        handler.addFilter(ConsoleFeedbackFilter())
        file_sink = logging.StreamHandler(diagnostics)
        client_logger = logging.getLogger('client')
        client_logger.addHandler(handler)
        client_logger.addHandler(file_sink)
        monkeypatch.setattr('core.client.manager.file_runner.console', console)
        monkeypatch.setattr('core.client.manager.file_runner.Config.file_separate_log', False)
        monkeypatch.setattr('core.client.manager.file_runner.sys.stdin', SimpleNamespace(isatty=lambda: False))
        monkeypatch.setattr('core.client.ui.TipsDisplay.show_file_tips', Mock())
        try:
            assert await runner.run() is False
        finally:
            client_logger.removeHandler(handler)
            client_logger.removeHandler(file_sink)
        output = terminal.getvalue()
        assert transcriber.failure_code == 'decode_failed'
        assert output.count(tr('file.failed')) == 1
        assert tr('file.failure.decode_failed.reason') in output
        assert tr('file.failure.decode_failed.action') in output
        summary = tr('file.summary_counts', value0=0, value1=1, value2=0)
        from rich.text import Text
        assert Text.from_markup(summary).plain in output
        assert 'ERROR' not in output
        assert 'RuntimeError' not in output
        assert 'File send failed' in diagnostics.getvalue()
        assert 'File child task failed' not in diagnostics.getvalue()

    asyncio.run(run())


def test_console_filter_keeps_unhandled_warnings_visible():
    filter_ = ConsoleFeedbackFilter()
    record = logging.LogRecord('client', logging.WARNING, '', 0, 'unhandled', (), None)
    assert filter_.filter(record)
    record.console_handled = True
    assert not filter_.filter(record)


@pytest.mark.parametrize('success', [False, True])
def test_failed_progress_is_removed_while_successful_progress_is_retained(factory, success):
    async def run():
        transcriber, _, _, _ = factory(FakeProcess())
        progress = SimpleNamespace(live=SimpleNamespace(transient=False), stop=Mock())
        transcriber._progress = progress
        if success:
            transcriber.summary = TranscriptionSummary(1, 1, 1, (), 1)
        await transcriber.close()
        assert progress.live.transient is not success
        progress.stop.assert_called_once()

    asyncio.run(run())
