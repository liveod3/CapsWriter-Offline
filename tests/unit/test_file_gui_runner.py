"""Mock child ownership and cancellation without devices, ASR, or user media."""

from io import BytesIO
import asyncio
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QProcess

from core.file_gui import runner as module
from core.file_gui.worker import _run
from core.file_gui.events import EventDecoder, EventSender, FRAME_LIMIT, encode_event


class CallbackSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in self.slots:
            slot(*args)


class FakeProcess:
    ProcessError = QProcess.ProcessError
    ExitStatus = QProcess.ExitStatus
    nullDevice = QProcess.nullDevice

    def __init__(self, parent):
        self.started = CallbackSignal()
        self.finished = CallbackSignal()
        self.errorOccurred = CallbackSignal()
        self.readyReadStandardOutput = CallbackSignal()
        self.buffer = bytearray()
        self.writes = []
        self.kills = 0
        self.input_closed = False
        self.deleted = False

    def setWorkingDirectory(self, path):
        self.cwd = path

    def setProcessEnvironment(self, environment):
        self.environment = environment

    def setStandardOutputFile(self, path):
        self.stdout = path

    def setStandardErrorFile(self, path):
        self.stderr = path

    def start(self, program, arguments):
        self.command = [program, *arguments]

    def processId(self):
        return 123

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def closeWriteChannel(self):
        self.input_closed = True

    def kill(self):
        self.kills += 1

    def deleteLater(self):
        self.deleted = True

    def bytesAvailable(self):
        return len(self.buffer)

    def read(self, limit):
        value = self.buffer[:limit]
        del self.buffer[:limit]
        return bytes(value)

    def event(self, value):
        self.buffer.extend(encode_event(value))
        self.readyReadStandardOutput.emit()


@pytest.fixture(scope='module')
def qt_app():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def runner(tmp_path, monkeypatch, qt_app):
    monkeypatch.setattr(module, 'QProcess', FakeProcess)
    runner = module.FileTranscriptionRunner(tmp_path)
    jobs = []

    def own(pid):
        assert pid == 123 and not runner._process.writes
        job = SimpleNamespace(closed=0)

        def close():
            job.closed += 1

        job.Close = close
        jobs.append(job)
        return job

    monkeypatch.setattr(module, '_own_process_tree', own)
    runner.test_jobs = jobs
    runner.test_results = []
    runner.finished.connect(lambda success, reason: runner.test_results.append((success, reason)))
    runner.test_file = tmp_path / '-sample with spaces.wav'
    runner.test_file.write_bytes(b'not real media')
    yield runner
    runner.shutdown()


def test_command_quotes_via_arguments_and_supports_pythonw(tmp_path, monkeypatch):
    monkeypatch.setattr(module.sys, 'executable', str(tmp_path / 'pythonw.exe'))
    monkeypatch.setattr(module.sys, 'frozen', False, raising=False)
    path = tmp_path / '-input with spaces.wav'
    command = module.worker_command(tmp_path, path, frozenset({'txt', 'srt'}))
    assert command == [str(tmp_path / 'python.exe'), str(tmp_path / 'start_client.py'),
                       '--file-worker', 'transcribe', '--format', 'srt,txt', '--', str(path)]


def test_frozen_command_uses_client_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(module.sys, 'executable', str(tmp_path / 'other.exe'))
    monkeypatch.setattr(module.sys, 'frozen', True, raising=False)
    command = module.worker_command(tmp_path, tmp_path / 'input.wav', frozenset({'srt'}))
    assert command[:2] == [str(tmp_path / 'start_client.exe'), '--file-worker']


def test_start_owns_child_before_gate_and_discards_private_console_output(runner):
    started = []
    runner.started.connect(lambda: started.append(True))
    assert runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    assert runner.active and not process.writes
    assert not hasattr(process, 'stdout') and process.stderr == QProcess.nullDevice()
    assert process.cwd == str(runner.root)
    process.started.emit()
    assert len(runner.test_jobs) == 1 and process.writes == [b'S'] and started == [True]
    assert not runner.start(runner.test_file, frozenset({'txt'}))


@pytest.mark.parametrize('code,status,expected', [
    (0, QProcess.ExitStatus.NormalExit, (True, 'completed')),
    (1, QProcess.ExitStatus.NormalExit, (False, 'failed')),
    (0, QProcess.ExitStatus.CrashExit, (False, 'failed')),
])
def test_completion_uses_exit_status_once_and_releases_tree(runner, code, status, expected):
    runner.start(runner.test_file, frozenset({'srt'}))
    process = runner._process
    process.started.emit()
    process.event({'type': 'completed', 'output_paths': ['synthetic.srt']})
    process.finished.emit(code, status)
    process.finished.emit(code, status)
    assert runner.test_results == [expected]
    assert not runner.active and process.deleted and runner.test_jobs[0].closed == 1


@pytest.mark.parametrize('formats', [frozenset(), frozenset({'other'})])
def test_invalid_options_do_not_spawn_or_complete(runner, formats):
    assert not runner.start(runner.test_file, formats)
    assert not runner.active and not runner.test_results


def test_missing_input_does_not_spawn(runner):
    assert not runner.start(runner.root / 'missing.wav', frozenset({'txt'}))
    assert not runner.active


def test_partial_override_does_not_replace_omitted_local_defaults(runner):
    assert runner.start(runner.test_file, frozenset({'txt'}), {'language': 'english'})
    command = runner._process.command
    index = command.index('--settings-json')
    assert json.loads(command[index + 1]) == {'language': 'english'}


def test_cancel_is_idempotent_and_success_exit_still_reports_cancelled(runner):
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    runner.cancel()
    runner.cancel()
    assert process.writes == [b'S', b'C'] and process.input_closed
    assert runner._timer.isActive()
    process.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert runner.test_results == [(False, 'cancelled')]
    assert not runner._timer.isActive()


def test_cancel_timeout_kills_owned_tree_and_then_completes(runner):
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    runner.cancel()
    runner._timer.timeout.emit()
    assert runner.test_jobs[0].closed == 1 and process.kills == 1
    assert runner.active and not runner.test_results
    process.finished.emit(1, QProcess.ExitStatus.CrashExit)
    assert runner.test_results == [(False, 'cancelled')]
    assert runner.test_jobs[0].closed == 1


def test_cancel_before_started_never_releases_boot_gate(runner, monkeypatch):
    monkeypatch.setattr(module, '_own_process_tree', lambda pid: None)
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    runner.cancel()
    process.started.emit()
    assert b'S' not in process.writes and process.kills == 1
    process.finished.emit(1, QProcess.ExitStatus.CrashExit)
    assert runner.test_results == [(False, 'cancelled')]


def test_ownership_failure_cannot_boot_client(runner, monkeypatch):
    def reject(pid):
        raise OSError('synthetic ownership failure')

    monkeypatch.setattr(module, '_own_process_tree', reject)
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    assert not process.writes and process.kills == 1
    process.finished.emit(1, QProcess.ExitStatus.CrashExit)
    assert runner.test_results == [(False, 'start_failed')]


def test_failed_start_and_stale_signals_cannot_complete_next_file(runner):
    runner.start(runner.test_file, frozenset({'txt'}))
    previous = runner._process
    previous.errorOccurred.emit(QProcess.ProcessError.FailedToStart)
    assert runner.test_results == [(False, 'start_failed')]
    assert runner.start(runner.test_file, frozenset({'srt'}))
    previous.started.emit()
    previous.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert runner.active and len(runner.test_results) == 1


def test_progress_is_forwarded_and_terminal_error_explains_failure(runner):
    updates = []
    runner.progress.connect(updates.append)
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    process.event({'type': 'progress', 'stage': 'connecting', 'processed_seconds': 0.0})
    assert updates[-1]['stage'] == 'connecting' and not runner._startup_timer.isActive()
    process.event({'type': 'failed', 'code': 'connection_failed'})
    process.finished.emit(1, QProcess.ExitStatus.NormalExit)
    assert runner.test_results == [(False, 'connection_failed')]


def test_success_requires_confirmed_output_metadata(runner):
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    process.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert runner.test_results == [(False, 'protocol_error')]


def test_output_paths_arriving_with_exit_are_delivered(runner):
    outputs = []
    progress = []
    runner.outputs.connect(outputs.append)
    runner.progress.connect(progress.append)
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    process.buffer.extend(encode_event({'type': 'completed', 'output_paths': ['synthetic.txt'],
                                       'text_length': 42, 'sequence': 2}))
    process.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert outputs == [['synthetic.txt']]
    assert progress[-1]['text_length'] == 42 and progress[-1]['sequence'] == 2
    assert runner.test_results == [(True, 'completed')]


@pytest.mark.parametrize('trailing', [b'{"type":', b'private console text', b'\xff'])
def test_completed_frame_with_truncated_trailing_data_never_claims_success(runner, trailing):
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    process.buffer.extend(encode_event({'type': 'completed', 'output_paths': ['synthetic.txt']}) + trailing)
    process.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert runner.test_results == [(False, 'protocol_error')]


def test_final_drain_remains_bounded_even_for_complete_frame_flood(runner):
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    process.buffer.extend(encode_event({'type': 'completed', 'output_paths': ['synthetic.txt']}))
    process.buffer.extend(encode_event({'type': 'ready'}) * 150000)
    before = process.bytesAvailable()
    process.finished.emit(0, QProcess.ExitStatus.NormalExit)
    assert runner.test_results == [(False, 'protocol_error')]
    assert 0 < before - process.bytesAvailable() <= FRAME_LIMIT * 64


@pytest.mark.parametrize('timer,reason', [('_startup_timer', 'startup_timeout'),
                                         ('_shutdown_timer', 'shutdown_timeout')])
def test_stalled_child_has_bounded_failure_and_tree_cleanup(runner, timer, reason):
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    getattr(runner, timer).timeout.emit()
    assert process.kills == 1 and runner.test_jobs[0].closed == 1
    process.finished.emit(1, QProcess.ExitStatus.CrashExit)
    assert runner.test_results == [(False, reason)]


def test_private_console_data_or_oversized_frame_fails_closed(runner):
    runner.start(runner.test_file, frozenset({'txt'}))
    process = runner._process
    process.started.emit()
    process.buffer.extend(b'a' * FRAME_LIMIT)
    process.readyReadStandardOutput.emit()
    assert process.kills == 1
    process.finished.emit(1, QProcess.ExitStatus.CrashExit)
    assert runner.test_results == [(False, 'protocol_error')]


def test_event_transport_handles_split_utf8_and_rejects_content():
    event = {'type': 'completed', 'output_paths': ['synthetic-例子.txt']}
    data = encode_event(event)
    decoder = EventDecoder()
    events = []
    for byte in data:
        events.extend(decoder.feed(bytes([byte])))
    assert events == [event]
    for value in ({'type': 'progress', 'stage': 'transcribing', 'text': 'private'},
                  {'type': 'progress', 'stage': 'transcribing', 'speed': float('nan')},
                  {'type': 'progress', 'stage': 'transcribing', 'speed': 10 ** 1000},
                  {'type': 'failed', 'code': 'private exception detail'},
                  {'type': 'completed', 'output_paths': ['x'] * 5}):
        with pytest.raises(ValueError):
            encode_event(value)


@pytest.mark.parametrize('value', [-1, True, 1.5, None, 10000001])
def test_chunk_count_metadata_requires_a_bounded_nonnegative_integer(value):
    with pytest.raises(ValueError):
        encode_event({'type': 'progress', 'stage': 'transcribing', 'chunks_completed': value})


@pytest.mark.parametrize('metadata', [{}, {'text_length': 0}, {'sequence': 1},
                                     {'text_length': 1234, 'sequence': 27}])
def test_optional_completion_counts_round_trip_without_text(metadata):
    event = {'type': 'completed', 'output_paths': ['synthetic.txt'], **metadata}
    assert EventDecoder().feed(encode_event(event)) == [event]


@pytest.mark.parametrize('field,value', [
    ('text_length', -1), ('text_length', True), ('text_length', 1.5),
    ('text_length', None), ('text_length', 'private transcript'),
    ('text_length', 10 ** 1000), ('sequence', 0), ('sequence', -1),
    ('sequence', True), ('sequence', 2.0), ('sequence', None),
    ('sequence', 'private transcript'), ('sequence', 10 ** 1000),
])
def test_invalid_completion_counts_are_rejected_by_sender_and_receiver(field, value):
    event = {'type': 'completed', 'output_paths': ['synthetic.txt'], field: value}
    with pytest.raises(ValueError):
        encode_event(event)
    with pytest.raises(ValueError):
        EventDecoder().feed(json.dumps(event).encode('utf-8') + b'\n')


@pytest.mark.parametrize('metadata', [{'text_length': 1}, {'sequence': 1}])
def test_completion_counts_are_not_allowed_in_progress_events(metadata):
    with pytest.raises(ValueError):
        encode_event({'type': 'progress', 'stage': 'transcribing', **metadata})


def test_event_sender_flushes_final_metadata_without_transcript():
    stream = BytesIO()
    sender = EventSender(stream)
    sender.send({'type': 'progress', 'stage': 'checking'})
    sender.send({'type': 'failed', 'code': 'decoder_unavailable'})
    sender.close()
    events = EventDecoder().feed(stream.getvalue())
    assert events[-1] == {'type': 'failed', 'code': 'decoder_unavailable'}
    assert not sender.thread.is_alive()


def test_worker_gate_prevents_client_imports():
    def forbidden(*args):
        pytest.fail('client initialized before ownership')

    assert _run([], BytesIO(b'').read, forbidden, forbidden) == 2


def test_worker_preserves_cli_failure_exit_status():
    release = threading.Event()
    reads = 0

    def read(size):
        nonlocal reads
        reads += 1
        if reads == 1:
            return b'S'
        release.wait(2)
        return b''

    app = SimpleNamespace(start=lambda: 1, stop=lambda: None)
    try:
        assert _run(['transcribe'], read, lambda command: app, lambda values: values) == 1
    finally:
        release.set()


def test_worker_cancellation_during_constructor_is_applied_before_start():
    cancelled = threading.Event()
    calls = []

    def read(size):
        if not calls:
            calls.append('gate')
            return b'S'
        cancelled.set()
        return b'C'

    def factory(command):
        assert cancelled.wait(2)
        return SimpleNamespace(stop=lambda: calls.append('stop'),
                               start=lambda: calls.append('start') or 0)

    assert _run([], read, factory, lambda values: values) == 0
    assert calls == ['gate', 'stop', 'start']


def test_worker_freezes_reloader_and_attaches_metadata_before_client_start():
    release = threading.Event()
    loop = asyncio.new_event_loop()
    calls = []
    events = []
    reads = 0

    def read(size):
        nonlocal reads
        reads += 1
        if reads == 1:
            return b'S'
        release.wait(2)
        return b''

    async def close():
        calls.append('freeze')

    def start():
        assert app.file_connect_timeout == 15.0
        calls.append('start')
        app.file_progress_callback({'type': 'failed', 'code': 'connection_failed'})
        return 1

    app = SimpleNamespace(loop=loop, config_reload=SimpleNamespace(close=close),
                          start=start, stop=lambda: None)
    try:
        assert _run([], read, lambda command: app, lambda values: values, emit=events.append) == 1
        assert calls == ['freeze', 'start']
        assert events[-1]['code'] == 'connection_failed'
    finally:
        release.set()
        loop.close()
