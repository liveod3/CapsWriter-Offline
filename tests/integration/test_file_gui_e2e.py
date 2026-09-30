"""Run the real Qt child, FFmpeg, and mock ASR while its control stdin stays open.

This preserves cold native imports after the S gate and catches the Windows
stdin-descriptor lock that previously stalled NumPy until cancellation or EOF.
"""

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import threading
import time
import wave

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows source runtime required')


@pytest.fixture(scope='module')
def qt_app():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_source(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and ffprobe are required for the synthetic decoder integration')
    # Copy public source only. The genuine entry point and relative imports now
    # own this temporary checkout, without bootstrap hooks or user configuration.
    shutil.copyfile(ROOT / 'start_client.py', tmp_path / 'start_client.py')
    excluded = {'__pycache__', 'server', 'logs', 'records'}
    for package in ('core', 'config_templates'):
        for source in (ROOT / package).rglob('*.py'):
            relative = source.relative_to(ROOT)
            if excluded.intersection(relative.parts) or any(part.startswith('.') for part in relative.parts):
                continue
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    dictionary = Path('core/tools/zhconv/zhcdict.json')
    shutil.copyfile(ROOT / dictionary, tmp_path / dictionary)
    media = tmp_path / 'synthetic sample.wav'
    with wave.open(str(media), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\x00\x00' * 16000 * 2)
    return tmp_path, media


def configure(root, port):
    source = (ROOT / 'config_templates/config_client_template.py').read_text(encoding='utf-8')
    replacements = {
        "port = '6016'": f"port = '{port}'",
        "ui_language = 'auto'": "ui_language = 'en'",
        "auth_token = os.environ.get('CAPSWRITER_AUTH_TOKEN', '')": "auth_token = ''",
        'save_diagnostic_logs = True': 'save_diagnostic_logs = False',
        'save_runtime_statistics = True': 'save_runtime_statistics = False',
        'save_transcripts = True': 'save_transcripts = False',
        'enable_tray = True': 'enable_tray = False',
    }
    for old, new in replacements.items():
        assert old in source
        source = source.replace(old, new)
    path = root / 'config_client.py'
    path.write_text(source, encoding='utf-8')
    return path.read_bytes()


class MockASR:
    def __init__(self, *, withhold_results=False):
        self.ready = threading.Event()
        self.messages = []
        self.errors = []
        self.port = None
        self.loop = None
        self.stop_event = None
        self.withhold_results = withhold_results
        self.thread = threading.Thread(target=self._thread_main, daemon=True)

    def _thread_main(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            self.errors.append(type(exc).__name__)
            self.ready.set()

    async def _serve(self):
        import websockets
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        # Match the real server's transport bound so normal 60-second float32
        # chunks fit after Base64 encoding (websockets defaults to only 1 MiB).
        async with websockets.serve(self._recognize, '127.0.0.1', 0, subprotocols=['binary'],
                                    max_size=6 * 1024 * 1024) as server:
            self.port = server.sockets[0].getsockname()[1]
            self.ready.set()
            await self.stop_event.wait()

    async def _recognize(self, connection, *_):
        duration = 0.0
        async for raw in connection:
            message = json.loads(raw)
            self.messages.append(message)
            duration += len(base64.b64decode(message['data'], validate=True)) / 64000
            if self.withhold_results:
                continue
            final = message['is_final']
            await asyncio.sleep(0.02)
            await connection.send(json.dumps({
                'task_id': message['task_id'], 'is_final': final, 'duration': duration,
                'time_start': message['time_start'], 'time_submit': time.time(),
                'time_complete': time.time(), 'text': 'Hello world.' if final else '',
                'text_accu': 'Hello world.' if final else '',
                'tokens': ['Hello', ' world', '.'] if final else [],
                'timestamps': [0.0, 0.5, 1.0] if final else [],
            }))

    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(5), 'Mock ASR did not start'
        assert not self.errors
        return self

    def __exit__(self, *_):
        if self.loop is not None and self.stop_event is not None:
            self.loop.call_soon_threadsafe(self.stop_event.set)
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()
        assert not self.errors


def run_file(qt_app, root, media, *, cancel_when=None, window=None, settings=None,
             preserve_window_formats=False):
    from core.file_gui.runner import FileTranscriptionRunner
    runner = window.runner if window is not None else FileTranscriptionRunner(root)
    progress, outputs, completions = [], [], []
    runner.progress.connect(progress.append)
    runner.outputs.connect(outputs.append)
    runner.finished.connect(lambda success, reason: completions.append((success, reason)))
    if settings is None:
        settings = {
            'language': 'english', 'file_seg_duration': 0.5, 'file_seg_overlap': 0.1,
            'file_max_inflight_chunks': 4, 'file_io_timeout': 3.0, 'file_result_timeout': 3.0,
        }
    started = time.monotonic()
    cancellation_sent = False
    if window is None:
        assert runner.start(media, frozenset({'txt', 'srt', 'json', 'merge'}), settings)
    else:
        window.options.update(settings)
        if not preserve_window_formats:
            for checkbox in window.formats.values():
                checkbox.setChecked(True)
        window.add_paths([str(media)])
        window.show()
        deadline = time.monotonic() + 5
        while window.importer.active and time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.005)
        assert window.entries and not window.importer.active
        window.start_batch()
        assert window.busy and runner.active
    try:
        # Keep stdin open throughout the genuine S-gate handshake. No C byte or
        # EOF may be needed to make the child start or flush a successful result.
        while not completions and time.monotonic() - started < 35:
            qt_app.processEvents()
            if cancel_when is not None and not cancellation_sent and cancel_when():
                runner.cancel()
                cancellation_sent = True
            time.sleep(0.005)
        assert completions, f'Child remained active; stages: {progress}'
        assert not runner.active
        assert len(completions) == 1
        return progress, outputs, completions[0]
    finally:
        if runner.active:
            runner.shutdown()
            deadline = time.monotonic() + 5
            while runner.active and time.monotonic() < deadline:
                qt_app.processEvents()
                time.sleep(0.005)
            assert not runner.active


def test_actual_qprocess_worker_decodes_reports_progress_and_writes_outputs(isolated_source, qt_app):
    from core.file_gui.runner import FileTranscriptionRunner
    from core.file_gui.window import FileTranscriptionWindow
    from core.file_progress import format_duration
    from core.i18n import tr

    root, media = isolated_source
    window = None
    try:
        with MockASR() as server:
            original = configure(root, server.port)
            window = FileTranscriptionWindow(FileTranscriptionRunner(root), {'port': str(server.port)})
            progress, output_groups, result = run_file(qt_app, root, media, window=window)
        assert result == (True, 'completed'), f'{result}; progress={progress}'
        final = progress[-1]
        assert final['type'] == 'completed'
        assert final['text_length'] == len('Hello world.') and final['sequence'] == 1
        assert not window.busy and window.current is None
        entry = window.last_completed
        assert entry is window.entries[0] and entry.state == 'completed'
        assert entry.metrics['elapsed_seconds'] == final['elapsed_seconds']
        assert entry.outputs == tuple(Path(value) for value in output_groups[0])
        assert window.open_button.isEnabled()
        assert window.progress.value() == 1000 and window.progress.chunks_completed == window.progress.chunks_total
        assert window.stage.text() == tr('files.state.idle')
        assert not window.processing_details.isVisible()
        elapsed = format_duration(final['elapsed_seconds'])
        speed = f"{final['processed_seconds'] / final['elapsed_seconds']:.2f}×"
        expected_metrics = tr('files.live_metrics', elapsed=elapsed, eta='00:00', speed=speed)
        assert window.metrics.text() == expected_metrics
        assert window.audio_metrics.text() == tr('files.audio_metrics', total='00:02',
                                                 processed='00:02', remaining='00:00')
        # Completed clocks retain backend timing when the normal UI timer refreshes.
        deadline = time.monotonic() + window.clock.interval() / 1000 + 0.05
        while time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.005)
        assert window.metrics.text() == expected_metrics
        assert window.progress.value() == 1000
        log = window.event_log.editor.toPlainText()
        expected_lines = [
            tr('files.log_file_start', index=1, total=1, name=media.name),
            tr('files.log_source', path=str(media)),
            tr('files.stage.transcribing'),
            tr('files.log_file_done', name=media.name, audio='00:02', elapsed=elapsed,
               speed=speed, rtf=f"{final['elapsed_seconds'] / final['processed_seconds']:.3f}"),
            tr('files.log_text_length', count=len('Hello world.')),
            *(tr('files.log_output', path=value) for value in output_groups[0]),
        ]
        positions = [log.index(line) for line in expected_lines]
        assert positions == sorted(positions)
        assert all(log.count(line) == 1 for line in expected_lines)
        assert 'Hello' not in log and 'world' not in log
    finally:
        if window is not None:
            window.close()
            qt_app.processEvents()
    assert result == (True, 'completed'), f'{result}; progress={progress}'
    assert (root / 'config_client.py').read_bytes() == original
    assert server.messages and server.messages[-1]['is_final']
    assert all(message['source'] == 'file' and message['language'] == 'english'
               and message['seg_duration'] == 0.5 and message['seg_overlap'] == 0.1
               and message['context'] == '' for message in server.messages)
    stages = {event.get('stage') for event in progress}
    assert {'starting', 'checking', 'connecting', 'probing', 'transcribing', 'saving'} <= stages
    advancing = [event for event in progress if event.get('processed_seconds', 0) > 0]
    assert advancing and any(0 < event['processed_seconds'] < 2 for event in advancing)
    final = progress[-1]
    assert final['type'] == 'completed'
    assert final['processed_seconds'] == pytest.approx(2.0)
    assert final['total_seconds'] == pytest.approx(2.0)
    assert final['elapsed_seconds'] > 0 and final['speed'] > 0
    assert len(output_groups) == 1
    paths = {Path(value) for value in output_groups[0]}
    assert paths == {media.with_suffix(suffix) for suffix in ('.txt', '.srt', '.json', '.merge.txt')}
    assert all(path.is_file() for path in paths)
    assert media.with_suffix('.txt').read_text(encoding='utf-8') == 'Hello world.'
    assert '-->' in media.with_suffix('.srt').read_text(encoding='utf-8')
    assert json.loads(media.with_suffix('.json').read_text(encoding='utf-8'))['tokens'] == ['Hello', ' world', '.']
    assert 'Hello world' not in repr(progress)
    assert not (root / 'logs').exists() and not (root / 'records').exists()


def test_untouched_window_exports_only_srt_despite_console_format_defaults(isolated_source, qt_app):
    from core.file_gui.runner import FileTranscriptionRunner
    from core.file_gui.window import FileTranscriptionWindow

    root, media = isolated_source
    window = None
    try:
        with MockASR() as server:
            original = configure(root, server.port)
            window = FileTranscriptionWindow(FileTranscriptionRunner(root), {
                'port': str(server.port), 'file_save_srt': False,
                'file_save_txt': True, 'file_save_json': True, 'file_save_merge': True,
            })
            _, outputs, result = run_file(
                qt_app, root, media, window=window, preserve_window_formats=True,
            )
        assert result == (True, 'completed')
        assert len(outputs) == 1 and len(outputs[0]) == 1
        subtitle = Path(outputs[0][0])
        assert subtitle.suffix == '.srt' and subtitle.is_file()
        assert not media.with_suffix('.txt').exists()
        assert not media.with_suffix('.json').exists()
        assert not media.with_suffix('.merge.txt').exists()
        assert (root / 'config_client.py').read_bytes() == original
    finally:
        if window is not None:
            window.close()
            qt_app.processEvents()


def test_unavailable_server_finishes_with_specific_failure(isolated_source, qt_app):
    root, media = isolated_source
    with socket.socket() as unavailable:
        # Reserve an unused endpoint without listening so no other test can take it.
        unavailable.bind(('127.0.0.1', 0))
        original = configure(root, unavailable.getsockname()[1])
        progress, outputs, result = run_file(qt_app, root, media)
    assert result == (False, 'connection_failed'), f'{result}; progress={progress}'
    assert any(event.get('stage') == 'connecting' for event in progress)
    assert outputs == []
    assert (root / 'config_client.py').read_bytes() == original
    assert not media.with_suffix('.txt').exists()


def test_actual_child_cancels_while_waiting_for_recognition(isolated_source, qt_app):
    root, media = isolated_source
    with MockASR(withhold_results=True) as server:
        configure(root, server.port)
        _, outputs, result = run_file(qt_app, root, media, cancel_when=lambda: bool(server.messages))
    assert server.messages
    assert result == (False, 'cancelled') and outputs == []
    assert not media.with_suffix('.txt').exists()


@pytest.mark.parametrize('abort', [False, True])
def test_window_pause_reorder_and_continue_real_children(isolated_source, qt_app, monkeypatch, abort):
    from PySide6.QtWidgets import QMessageBox
    from core.file_gui.runner import FileTranscriptionRunner
    from core.file_gui.window import FileTranscriptionWindow

    root, media = isolated_source
    paths = [media, root / 'second.wav', root / 'third.wav']
    for path in paths[1:]:
        shutil.copyfile(media, path)

    def wait_until(predicate):
        deadline = time.monotonic() + 35
        while not predicate() and time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.005)
        assert predicate(), 'File GUI did not reach the expected state'

    window = None
    try:
        with MockASR(withhold_results=abort) as server:
            original = configure(root, server.port)
            runner = FileTranscriptionRunner(root)
            window = FileTranscriptionWindow(runner, {'port': str(server.port)})
            window.options.update(file_seg_duration=0.5, file_seg_overlap=0.1,
                                  file_io_timeout=3.0, file_result_timeout=3.0)
            output_groups = []
            runner.outputs.connect(output_groups.append)
            window.add_paths(list(map(str, paths)))
            wait_until(lambda: not window.importer.active)
            window.start_batch()
            if abort:
                wait_until(lambda: bool(server.messages))
                monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Yes)
                window.stop_button.click()
            else:
                window.pause_button.click()
            wait_until(lambda: window.paused and not runner.active)
            assert not window.busy
            assert len(output_groups) == (0 if abort else 1)
            if abort:
                assert window.entries[0].metrics == {} and window.entries[0].state == 'cancelled'
                server.withhold_results = False
            else:
                assert window.entries[0].state == 'completed'
                assert Path(output_groups[0][0]).is_file()
            window.move_rows([2], 0)
            expected = [paths[2], paths[0], paths[1]] if abort else [paths[0], paths[2], paths[1]]
            assert [entry.path for entry in window.entries] == expected
            window.start_button.click()
            wait_until(lambda: not window.busy and not runner.active)
            assert not window.paused
            assert all(entry.state == 'completed' for entry in window.entries)
            assert [Path(group[0]).stem for group in output_groups] == [path.stem for path in expected]
            assert len(output_groups) == 3
            assert all(Path(group[0]).is_file() for group in output_groups)
            assert (root / 'config_client.py').read_bytes() == original
    finally:
        if window is not None:
            window.stop_batch()
            deadline = time.monotonic() + 10
            while window.runner.active and time.monotonic() < deadline:
                qt_app.processEvents()
                time.sleep(0.005)
            window.close()


def test_invalid_media_reports_decoder_failure_without_outputs(isolated_source, qt_app):
    root, media = isolated_source
    media.write_bytes(b'Synthetic invalid media')
    with MockASR() as server:
        configure(root, server.port)
        _, outputs, result = run_file(qt_app, root, media)
    assert result == (False, 'decode_failed') and outputs == []
    assert not media.with_suffix('.txt').exists()


def test_default_window_sends_60_second_chunks_and_displays_mock_confirmations(isolated_source, qt_app):
    """Validate transport and GUI updates; mock echoes do not model genuine ASR cadence."""
    from core.file_gui.runner import FileTranscriptionRunner
    from core.file_gui.window import FileTranscriptionWindow

    root, media = isolated_source
    with wave.open(str(media), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\x00\x00' * 16000 * 140)
    window = None
    confirmed = []
    displayed = []
    try:
        with MockASR() as server:
            original = configure(root, server.port)
            runner = FileTranscriptionRunner(root)
            window = FileTranscriptionWindow(runner, {'port': str(server.port)})
            assert window.options['file_seg_duration'] == 60
            assert window.options['file_seg_overlap'] == 4

            def observe(event):
                processed = event.get('processed_seconds', 0)
                if processed > (confirmed[-1] if confirmed else 0):
                    confirmed.append(processed)
                    displayed.append(window.progress.value())

            # The window is already connected, so observe its rendered value
            # after each genuine worker event, without injecting progress.
            runner.progress.connect(observe)
            progress, output_groups, result = run_file(qt_app, root, media, window=window, settings={})
        assert result == (True, 'completed'), f'{result}; progress={progress}'
        assert (root / 'config_client.py').read_bytes() == original
        assert len(server.messages) == 4 and server.messages[-1]['is_final']
        assert all(message['seg_duration'] == 60 and message['seg_overlap'] == 4
                   for message in server.messages)
        assert all(not message['is_final'] for message in server.messages[:-1])
        durations = [len(base64.b64decode(message['data'], validate=True)) / 64000
                     for message in server.messages[:-1]]
        assert durations == [60, 60, 20]
        assert server.messages[-1]['data'] == ''
        assert confirmed == [60, 120, 140]
        assert displayed == [round(seconds / 140 * 1000) for seconds in confirmed]
        assert progress[-1]['type'] == 'completed'
        assert progress[-1]['processed_seconds'] == 140
        assert progress[-1]['total_seconds'] == 140
        assert window.progress.value() == 1000 and not window.busy
        assert len(output_groups) == 1
        paths = {Path(value) for value in output_groups[0]}
        assert paths == {media.with_suffix(suffix) for suffix in ('.txt', '.srt', '.json', '.merge.txt')}
        assert all(path.is_file() for path in paths)
        assert set(window.last_completed.outputs) == paths
        assert media.with_suffix('.txt').read_text(encoding='utf-8') == 'Hello world.'
        assert 'Hello world' not in repr(progress)
        assert 'Hello world' not in window.event_log.editor.toPlainText()
        assert not (root / 'logs').exists() and not (root / 'records').exists()
    finally:
        if window is not None:
            window.close()
            qt_app.processEvents()
