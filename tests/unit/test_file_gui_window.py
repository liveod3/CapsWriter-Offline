"""Exercise explicit file gestures and queue ownership without desktop input or ASR."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, QObject, QPoint, QPointF, Qt, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QMessageBox


class FakeRunner(QObject):
    finished = Signal(bool, str)
    progress = Signal(dict)
    outputs = Signal(list)

    def __init__(self):
        super().__init__()
        self.calls = []
        self.options = []
        self.cancel_count = 0
        self.active = False
        self.reject = set()

    def start(self, path, formats, options=None):
        assert not self.active
        self.calls.append((path, formats))
        self.options.append(dict(options or {}))
        self.active = path not in self.reject
        return self.active

    def cancel(self):
        self.cancel_count += 1

    def complete(self, success=True, reason='completed'):
        assert self.active
        self.active = False
        self.finished.emit(success, reason)


@pytest.fixture(scope='module')
def qt_app():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qt_app, monkeypatch):
    from core.file_gui.window import FileTranscriptionWindow

    def unexpected_clipboard_read():
        pytest.fail('Clipboard access must follow an explicit paste action')

    monkeypatch.setattr(QApplication, 'clipboard', unexpected_clipboard_read)
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Yes)
    runner = FakeRunner()
    widget = FileTranscriptionWindow(runner)
    widget.show()
    qt_app.processEvents()
    import_paths = widget.add_paths

    def add_and_wait(paths):
        import_paths(paths)
        deadline = time.perf_counter() + 5
        while widget.importer.active and time.perf_counter() < deadline:
            qt_app.processEvents()
            time.sleep(0.001)
        assert not widget.importer.active

    monkeypatch.setattr(widget, 'add_paths', add_and_wait)
    yield widget
    if runner.active:
        widget.stop_batch()
        runner.complete(False, 'cancelled')
    widget.clock.stop()
    widget.close()
    qt_app.processEvents()


@pytest.fixture
def media(tmp_path):
    paths = [tmp_path / name for name in ('first clip.wav', 'second clip.mp4', 'third clip.flac')]
    for path in paths:
        path.write_bytes(b'synthetic fixture')
    return paths


def file_mime(paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def drag_enter(widget, mime):
    event = QDragEnterEvent(QPoint(80, 180), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)
    return event


def drop(widget, mime):
    event = QDropEvent(QPointF(80, 180), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)
    return event


def test_file_drag_hint_leave_and_drop_import_exact_paths(window, media):
    mime = file_mime(media[:2])
    idle_title = window.drop_title.text()
    entered = drag_enter(window, mime)
    assert entered.isAccepted()
    assert window.drop_zone.property('dragging') is True
    assert window.drop_title.text() != idle_title
    assert window.entries == []

    QApplication.sendEvent(window, QDragLeaveEvent())
    assert window.drop_zone.property('dragging') is False
    assert window.drop_title.text() == idle_title

    drag_enter(window, mime)
    dropped = drop(window, mime)
    assert dropped.isAccepted()
    assert window.drop_zone.property('dragging') is False
    assert [entry.path for entry in window.entries] == media[:2]
    assert window.runner.calls == []


@pytest.mark.parametrize('content', ['https://example.invalid/media.wav', 'ordinary clipboard text'])
def test_remote_or_text_drag_never_accepts_or_queues(window, content):
    mime = QMimeData()
    if content.startswith('https:'):
        mime.setUrls([QUrl(content)])
    else:
        mime.setText(content)
    assert not drag_enter(window, mime).isAccepted()
    assert window.entries == []
    assert window.runner.calls == []


def test_explicit_clipboard_file_urls_and_quoted_paths_are_deduplicated(window, media, monkeypatch):
    state = {'mime': file_mime(media[:2]), 'reads': 0}

    def clipboard():
        state['reads'] += 1
        return SimpleNamespace(mimeData=lambda: state['mime'])

    monkeypatch.setattr(QApplication, 'clipboard', clipboard)
    window.paste_button.click()
    assert state['reads'] == 1
    assert [entry.path for entry in window.entries] == media[:2]

    text = QMimeData()
    text.setText(f'"{media[1]}"\n"{media[2]}"\nordinary clipboard text')
    state['mime'] = text
    window.paste_shortcut.activated.emit()
    assert state['reads'] == 2
    assert [entry.path for entry in window.entries] == media
    assert window.runner.calls == []


@pytest.mark.parametrize('empty_mime', [None, QMimeData()])
def test_empty_clipboard_paste_is_harmless(window, monkeypatch, empty_mime):
    monkeypatch.setattr(QApplication, 'clipboard',
                        lambda: SimpleNamespace(mimeData=lambda: empty_mime))
    window.paste_files()
    assert window.entries == [] and window.runner.calls == []


def test_invalid_paths_directories_and_queue_overflow_are_skipped(window, media, tmp_path, monkeypatch):
    from core.file_gui import window as module
    monkeypatch.setattr(module, 'MAX_FILES', 2)
    text = tmp_path / 'notes.txt'
    text.write_text('synthetic', encoding='utf-8')
    window.add_paths([str(text), 'relative.wav', str(tmp_path / 'missing.wav')])
    assert window.entries == []
    window.add_paths([str(path) for path in media])
    assert [entry.path for entry in window.entries] == media[:2]
    assert window.file_list.count() == 2


def test_batch_uses_format_snapshot_serializes_files_and_allows_failure_retry(window, media):
    window.add_paths([str(path) for path in media])
    window.formats['srt'].setChecked(False)
    window.formats['txt'].setChecked(True)
    window.formats['json'].setChecked(False)
    window.start_button.click()
    assert window.busy and window.current.path == media[0]
    assert window.runner.calls == [(media[0], frozenset({'txt'}))]
    assert all(not box.isEnabled() for box in window.formats.values())
    assert not window.clear_button.isEnabled()
    window.start_batch()
    assert len(window.runner.calls) == 1

    window.runner.complete(False, 'failed')
    assert window.current.path == media[1]
    assert window.entries[0].state == 'failed'
    window.runner.complete()
    window.runner.complete()
    assert not window.busy and window.current is None
    assert [entry.state for entry in window.entries] == ['failed', 'completed', 'completed']
    assert window.runner.calls == [(path, frozenset({'txt'})) for path in media]

    window.start_batch()
    assert window.current.path == media[0]
    window.runner.complete()
    assert not window.busy and not window.start_button.isEnabled()


def test_start_failure_stops_batch_and_preserves_remaining_file(window, media):
    window.add_paths([str(path) for path in media[:2]])
    window.runner.reject.add(media[0])
    window.start_batch()
    assert window.entries[0].state == 'failed'
    assert window.current is None and not window.busy
    assert window.entries[1].state == 'pending'
    assert len(window.runner.calls) == 1


def test_stop_waits_for_active_file_and_does_not_start_the_next_file(window, media):
    window.add_paths([str(path) for path in media])
    window.start_batch()
    window.stop_button.click()
    window.stop_batch()
    assert window.busy and window.stopping
    assert not window.stop_button.isEnabled()
    assert window.runner.cancel_count == 1
    assert len(window.runner.calls) == 1
    window.runner.complete(False, 'cancelled')
    assert not window.busy and window.current is None
    assert [entry.state for entry in window.entries] == ['cancelled', 'pending', 'pending']
    assert len(window.runner.calls) == 1
    window.start_batch()
    assert window.current.path == media[0]
    assert not window.stopping


def test_files_added_during_batch_wait_for_explicit_next_start(window, media):
    window.add_paths([str(media[0])])
    window.start_batch()
    window.add_paths([str(media[1])])
    window.runner.complete()
    assert not window.busy
    assert len(window.runner.calls) == 1
    assert window.entries[1].state == 'pending'
    assert window.start_button.isEnabled()
    window.start_batch()
    assert window.current.path == media[1]


def test_no_formats_cannot_start_or_modify_queue(window, media):
    window.add_paths([str(media[0])])
    for checkbox in window.formats.values():
        checkbox.setChecked(False)
    assert not window.start_button.isEnabled()
    window.start_batch()
    assert not window.busy and window.runner.calls == []
    assert window.entries[0].state == 'pending'


def test_close_active_batch_defers_window_close_until_runner_finishes(window, media, qt_app):
    window.add_paths([str(path) for path in media])
    window.start_batch()
    assert not window.close()
    assert window.closing and window.busy and not window.isEnabled()
    assert window.isVisible()
    assert window.runner.cancel_count == 1
    assert not drag_enter(window, file_mime(media)).isAccepted()
    window.paste_files()
    window.runner.complete(False, 'cancelled')
    qt_app.processEvents()
    assert not window.isVisible() and not window.busy
    assert len(window.runner.calls) == 1


def test_folder_open_is_explicit(window, media, monkeypatch):
    from core.file_gui import window as module
    opened = []
    monkeypatch.setattr(module.QDesktopServices, 'openUrl', lambda url: opened.append(url) or True)
    window.add_paths([str(media[0])])
    assert opened == []
    window.open_button.click()
    assert [Path(url.toLocalFile()) for url in opened] == [media[0].parent]
    assert window.runner.calls == []


def test_progress_uses_confirmed_duration_not_elapsed_time_and_waits_for_finish(window, media):
    window.add_paths([str(media[0])])
    window.start_batch()
    window.runner.progress.emit({
        'type': 'progress', 'stage': 'transcribing', 'processed_seconds': 10,
        'total_seconds': 40, 'elapsed_seconds': 2, 'eta_seconds': 6,
    })
    assert window.progress.minimum() == 0 and window.progress.maximum() == 1000
    assert window.progress.value() == 250 and window.progress.chunks_completed == 1 and window.progress.chunks_total == 2
    assert '00:10' in window.audio_metrics.text() and '00:40' in window.audio_metrics.text()
    window.started_at -= 20
    window.last_event_at -= 6
    window.update_metrics()
    assert window.progress.value() == 250
    assert window.current.metrics['processed_seconds'] == 10
    assert not hasattr(window, 'connection')

    window.runner.progress.emit({
        'type': 'completed', 'processed_seconds': 40, 'total_seconds': 40,
        'elapsed_seconds': 8, 'speed': 5, 'eta_seconds': 0,
    })
    assert window.busy and window.progress.value() == 1000
    assert window.current.state == 'running'
    window.runner.complete()
    assert window.progress.value() == 1000 and window.progress.chunks_completed == window.progress.chunks_total


def test_unknown_total_keeps_percentage_unknown_and_new_file_resets_progress(window, media):
    from core.i18n import tr
    window.add_paths([str(path) for path in media[:2]])
    window.start_batch()
    window.runner.progress.emit({
        'type': 'progress', 'stage': 'transcribing', 'processed_seconds': 12,
        'total_seconds': None, 'elapsed_seconds': 2,
    })
    assert window.progress.value() == 0 and window.percentage.text() == tr('files.chunk_count_unknown', completed=1)
    assert '00:12' in window.audio_metrics.text()
    window.runner.complete()
    assert window.current.path == media[1]
    assert window.current.metrics['processed_seconds'] == 0
    assert window.progress.value() == 0


@pytest.mark.parametrize('reason', ['connection_failed', 'decoder_unavailable', 'startup_timeout'])
def test_common_failures_stop_queue_and_explain_action(window, media, reason):
    from core.file_gui.window import failure_text
    window.add_paths([str(path) for path in media])
    window.start_batch()
    window.runner.complete(False, reason)
    assert not window.busy and window.current is None
    assert [entry.state for entry in window.entries] == ['failed', 'pending', 'pending']
    assert window.entries[0].failure == reason
    assert window.detail.text() == failure_text(reason)
    assert len(window.runner.calls) == 1
    assert window.start_button.isEnabled() and window.settings_button.isEnabled()


def test_effective_settings_are_frozen_for_entire_batch_and_not_saved(window, media):
    window.add_paths([str(path) for path in media[:2]])
    window.options.update(language='english', file_seg_duration=20, file_seg_overlap=2,
                          file_max_inflight_chunks=2, file_io_timeout=15, file_result_timeout=90)
    expected = dict(window.options)
    original = dict(window.config)
    window.start_batch()
    assert not window.settings_button.isEnabled()
    window.options['language'] = 'japanese'
    window.runner.complete()
    assert window.runner.options == [expected, expected]
    assert window.config == original


@pytest.mark.parametrize('changes', [
    {'file_seg_duration': 120}, {'file_seg_overlap': 60}, {'file_max_inflight_chunks': 1},
    {'file_result_timeout': 0}, {'language': ''},
])
def test_invalid_effective_settings_cannot_launch_child(window, media, changes):
    window.add_paths([str(media[0])])
    window.options.update(changes)
    window.start_batch()
    assert not window.busy and window.runner.calls == []
    assert window.entries[0].state == 'pending'
    assert window.notice.text()


def test_outputs_remain_discoverable_with_only_explicit_folder_open(window, media, monkeypatch):
    from core.file_gui import window as module
    outputs = [media[0].with_name('first clip (2).txt'), media[0].with_name('first clip (2).srt')]
    for output in outputs:
        output.write_text('Synthetic result', encoding='utf-8')
    opened = []
    monkeypatch.setattr(module.QDesktopServices, 'openUrl', lambda url: opened.append(url) or True)
    window.add_paths([str(media[0])])
    window.start_batch()
    window.runner.outputs.emit(list(map(str, outputs)))
    window.runner.complete()
    assert opened == [] and window.entries[0].outputs == tuple(outputs)
    assert not hasattr(window, 'result_button')
    assert window.open_button.isEnabled()
    assert str(outputs[0]) in window.detail.toolTip()
    window.open_button.click()
    assert [Path(url.toLocalFile()) for url in opened] == [media[0].parent]


def test_settings_dialog_rejects_flow_control_deadlock_and_accepts_repaired_values(qt_app):
    from core.file_gui.options import DEFAULTS
    from core.file_gui.settings import FileOptionsDialog
    original = dict(DEFAULTS)
    dialog = FileOptionsDialog(original, 'wss://example.invalid:6016')
    dialog.fields['file_max_inflight_chunks'].setValue(1)
    dialog.accept()
    assert not dialog.result() and dialog.error.text()
    assert original == DEFAULTS and dialog.values == DEFAULTS
    dialog.fields['file_max_inflight_chunks'].setValue(4)
    dialog.fields['language'].set_config_value('english')
    dialog.fields['file_result_timeout'].setValue(120)
    dialog.accept()
    assert dialog.result()
    assert dialog.values['language'] == 'english'
    assert dialog.values['file_result_timeout'] == 120
    assert original == DEFAULTS
    dialog.close()


def test_clearing_completed_queue_resets_progress_and_status(window, media):
    from core.i18n import tr
    window.add_paths([str(media[0])])
    window.start_batch()
    window.runner.progress.emit({'type': 'completed', 'processed_seconds': 40, 'total_seconds': 40})
    window.runner.complete()
    assert window.progress.value() == 1000
    window.clear_button.click()
    assert not window.entries and window.last_completed is None
    assert window.progress.value() == 0
    assert window.stage.text() == tr('files.state.idle')


def test_gui_clocks_match_terminal_without_advancing_confirmed_audio(window, media, monkeypatch):
    from core.file_gui import window as module
    from core.i18n import tr
    now = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    window.add_paths([str(media[0])])
    window.start_batch()
    now[0] += 30  # Startup and probing must not inflate recognition time.
    window.runner.progress.emit({
        'type': 'progress', 'stage': 'transcribing', 'processed_seconds': 20,
        'total_seconds': 100, 'elapsed_seconds': 4, 'eta_seconds': 16, 'speed': 5,
    })
    assert window.metrics.text() == tr('files.live_metrics', elapsed='00:04', eta='00:16', speed='5.00×')
    now[0] += 2
    window.update_metrics()
    assert window.metrics.text() == tr('files.live_metrics', elapsed='00:06', eta='00:14', speed='5.00×')
    assert window.chunk_metrics.text() == tr('files.speed_metrics', average='5.00×', last='—')
    assert window.audio_metrics.text() == tr('files.audio_metrics', total='01:40', processed='00:20', remaining='01:20')
    assert window.progress.value() == 200
    window.runner.progress.emit({
        'type': 'completed', 'processed_seconds': 100, 'total_seconds': 100,
        'elapsed_seconds': 12, 'eta_seconds': 0, 'speed': 100 / 12,
    })
    now[0] += 3  # Process cleanup does not alter the backend's final summary.
    window.update_metrics()
    assert window.metrics.text() == tr('files.live_metrics', elapsed='00:12', eta='00:00', speed='8.33×')
    assert window.busy
    window.runner.complete()
    assert window.metrics.text() == tr('files.live_metrics', elapsed='00:12', eta='00:00', speed='8.33×')


def test_activity_log_records_ordered_file_outcomes_without_transcript(window, media):
    from core.i18n import tr
    window.add_paths([str(path) for path in media[:2]])
    window.start_batch()
    event = {'type': 'progress', 'stage': 'transcribing', 'processed_seconds': 10,
             'total_seconds': 40, 'elapsed_seconds': 2, 'eta_seconds': 6}
    window.runner.progress.emit(event)
    window.runner.progress.emit(event)
    output = str(media[0].with_name('first clip (2).txt'))
    window.runner.progress.emit({
        'type': 'completed', 'processed_seconds': 40, 'total_seconds': 40,
        'elapsed_seconds': 8, 'eta_seconds': 0, 'text_length': 123, 'sequence': 2,
        'text': 'PRIVATE TRANSCRIPT MUST NOT BE DISPLAYED',
    })
    window.runner.outputs.emit([output])
    window.runner.complete()
    window.runner.complete(False, 'decode_failed')
    log = window.event_log.editor.toPlainText()
    expected = [
        tr('files.log_file_start', index=1, total=2, name=media[0].name),
        tr('files.stage.transcribing'),
        tr('files.log_duration', duration='00:40'),
        tr('files.log_progress', processed='00:10', total='00:40', elapsed='00:02', speed='5.00×'),
        tr('files.stage.finishing'),
        tr('files.log_file_done', name=media[0].name, audio='00:40', elapsed='00:08', speed='5.00×', rtf='0.200'),
        tr('files.log_numbered', sequence=2),
        tr('files.log_text_length', count=123),
        tr('files.log_output', path=output),
        tr('files.log_file_start', index=2, total=2, name=media[1].name),
    ]
    positions = [log.index(message) for message in expected]
    assert positions == sorted(positions)
    assert log.count(tr('files.stage.transcribing')) == 1
    assert log.count(tr('files.log_duration', duration='00:40')) == 1
    assert log.count(expected[3]) == 1
    assert 'PRIVATE TRANSCRIPT' not in log
    assert tr('file.failure.decode_failed.reason') in log
    assert str(media[0]) in log
    assert not window.busy


def test_actual_timeout_still_marks_file_failed_and_records_error(window, media):
    from core.file_gui.window import failure_text

    window.add_paths([str(media[0])])
    window.start_batch()
    window.runner.complete(False, 'timeout')
    assert not window.busy and window.entries[0].state == 'failed'
    assert window.entries[0].failure == 'timeout'
    assert failure_text('timeout') in window.event_log.editor.toPlainText()


def test_normal_chunk_wait_is_quiet_and_cancel_is_recorded(window, media, monkeypatch):
    from core.file_gui import window as module
    from core.i18n import tr
    now = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    window.add_paths([str(path) for path in media[:2]])
    window.start_batch()
    window.runner.progress.emit({'type': 'progress', 'stage': 'transcribing',
                                 'processed_seconds': 10, 'total_seconds': 40, 'elapsed_seconds': 2})
    before_wait = window.event_log.editor.toPlainText()
    for _ in range(20):
        now[0] += 1
        window.update_metrics()
    assert window.event_log.editor.toPlainText() == before_wait
    assert window.progress.value() == 250
    window.runner.progress.emit({'type': 'progress', 'stage': 'transcribing',
                                 'processed_seconds': 20, 'total_seconds': 40, 'elapsed_seconds': 22})
    before_wait = window.event_log.editor.toPlainText()
    now[0] += 11
    window.update_metrics()
    assert window.event_log.editor.toPlainText() == before_wait
    window.stop_batch()
    window.runner.complete(False, 'cancelled')
    log = window.event_log.editor.toPlainText()
    assert tr('files.log_cancel_requested') in log
    assert tr('files.log_file_cancelled', name=media[0].name, elapsed='00:31') in log
    assert tr('files.log_batch_end', total=2, succeeded=0, failed=0, pending=2,
              audio='00:00', elapsed='00:31', speed='0.00×') in log


def test_completed_file_clears_stale_eta_and_owns_progress_title_after_prior_failure(window, media):
    from core.i18n import tr
    window.add_paths([str(path) for path in media[:2]])
    window.start_batch()
    window.runner.complete(False, 'decode_failed')
    window.runner.progress.emit({'type': 'progress', 'stage': 'saving', 'processed_seconds': 90,
                                 'total_seconds': 100, 'elapsed_seconds': 18, 'eta_seconds': 2})
    assert window.progress.value() == 900
    window.runner.progress.emit({'type': 'completed', 'processed_seconds': 90,
                                 'total_seconds': 90, 'elapsed_seconds': 18})
    assert window.metrics.text() == tr('files.live_metrics', elapsed='00:18', eta='00:00', speed='5.00×')
    window.runner.complete()
    assert window.stage.text() == tr('files.state.idle')
    assert not window.processing_details.isVisible()
    assert window.notice.text() == tr('files.batch_failed')
    assert window.progress.value() == 1000


@pytest.mark.parametrize('gesture', ['drag', 'clipboard_urls', 'clipboard_text', 'picker'])
def test_import_gestures_add_media_and_deduplicate(window, media, tmp_path, monkeypatch, gesture):
    from core.file_gui import window as module

    folder = tmp_path / 'import folder'
    nested = folder / 'nested'
    nested.mkdir(parents=True)
    first, second = folder / 'a.wav', nested / 'b.MP4'
    first.write_bytes(b'synthetic media')
    second.write_bytes(b'synthetic media')
    (folder / 'a.txt').write_text('synthetic output', encoding='utf-8')
    (nested / 'b.json').write_text('{}', encoding='utf-8')
    window.add_paths([str(media[0])])
    paths = [folder, first, folder]
    if gesture == 'drag':
        mime = file_mime(paths)
        assert drag_enter(window, mime).isAccepted()
        assert drop(window, mime).isAccepted()
    elif gesture.startswith('clipboard_'):
        mime = file_mime(paths)
        if gesture == 'clipboard_text':
            mime = QMimeData()
            mime.setText('\n'.join(f'"{path}"' for path in paths))
        monkeypatch.setattr(QApplication, 'clipboard', lambda: SimpleNamespace(mimeData=lambda: mime))
        window.paste_button.click()
    else:
        monkeypatch.setattr(module, 'choose_paths', lambda *_: [str(first), str(second)])
        window.add_button.click()
        window.add_button.click()
    assert [entry.path for entry in window.entries] == [media[0], first, second]
    assert window.file_list.count() == 3
    assert window.runner.calls == []
    assert not window.importer.active


@pytest.mark.parametrize('failed', [False, True])
def test_file_picker_cancel_or_failure_retains_queue_and_window(window, media, monkeypatch, failed):
    from core.file_gui import window as module
    from core.i18n import tr

    window.add_paths([str(media[0])])

    def choose(*_):
        if failed:
            raise RuntimeError('Synthetic native picker failure')
        return []

    monkeypatch.setattr(module, 'choose_paths', choose)
    window.add_button.click()
    assert [entry.path for entry in window.entries] == [media[0]]
    assert window.start_button.isEnabled() and window.add_button.isEnabled()
    assert not window.importer.active and not window.runner.calls
    if failed:
        assert window.notice.text() == tr('files.picker_unavailable')
        assert 'Synthetic native picker failure' not in window.event_log.editor.toPlainText()


def test_folder_import_honors_configured_nonrecursive_scan_and_later_changes(window, tmp_path):
    folder = tmp_path / 'folder'
    nested = folder / 'nested'
    nested.mkdir(parents=True)
    first, second = folder / 'a.wav', nested / 'b.wav'
    first.write_bytes(b'synthetic media')
    second.write_bytes(b'synthetic media')
    window.config['file_scan_recursive'] = False
    window.add_paths([str(folder)])
    assert [entry.path for entry in window.entries] == [first]
    window.config['file_scan_recursive'] = True
    window.add_paths([str(folder)])
    assert [entry.path for entry in window.entries] == [first, second]


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
def test_status_card_is_minimal_while_idle_and_shows_details_only_during_work(window, media, outcome):
    from core.i18n import tr

    def assert_idle():
        assert window.stage.text() == tr('files.state.idle')
        assert window.idle_hint.isVisible()
        assert not window.processing_details.isVisible()
        assert not window.percentage.isVisible()

    assert_idle()
    window.add_paths([str(media[0])])
    assert_idle()
    window.start_batch()
    assert window.stage.text() == tr('files.state.processing')
    assert not window.idle_hint.isVisible()
    assert window.processing_details.isVisible() and window.percentage.isVisible()
    window.runner.progress.emit({'type': 'progress', 'stage': 'transcribing',
                                 'processed_seconds': 1, 'total_seconds': 2, 'elapsed_seconds': 1})
    assert window.phase.text() == tr('files.stage.transcribing')
    if outcome == 'completed':
        window.runner.progress.emit({'type': 'completed', 'processed_seconds': 2,
                                     'total_seconds': 2, 'elapsed_seconds': 1})
        window.runner.complete()
    elif outcome == 'failed':
        window.runner.complete(False, 'decode_failed')
    else:
        window.stop_button.click()
        assert window.processing_details.isVisible()
        window.runner.complete(False, 'cancelled')
    assert not window.busy and window.entries[0].state == outcome
    assert_idle()


def _hold_import_scan(monkeypatch):
    """Hold the real scanner at a cooperative boundary without blocking Qt."""
    import threading
    from core.file_gui import imports

    entered, release, cancellation_seen = threading.Event(), threading.Event(), threading.Event()
    original = imports.scan_paths

    def controlled_scan(**arguments):
        entered.set()
        deadline = time.perf_counter() + 5
        while not release.wait(0.002) and time.perf_counter() < deadline:
            if arguments['cancelled']():
                cancellation_seen.set()
        return original(**arguments)

    monkeypatch.setattr(imports, 'scan_paths', controlled_scan)
    return SimpleNamespace(entered=entered, release=release, cancellation_seen=cancellation_seen)


def _wait_for_import(window, qt_app):
    deadline = time.perf_counter() + 5
    while window.importer.active and time.perf_counter() < deadline:
        qt_app.processEvents()
        time.sleep(0.001)
    assert not window.importer.active


def test_import_disables_conflicting_controls_and_ignores_repeated_gestures(window, media, monkeypatch, qt_app):
    window.add_paths([str(media[0])])
    held = _hold_import_scan(monkeypatch)
    type(window).add_paths(window, [str(media[1])])
    try:
        assert held.entered.wait(2) and window.importer.active
        controls = (window.start_button, window.clear_button,
                    window.add_button, window.paste_button)
        assert all(not control.isEnabled() for control in controls)
        window.start_batch()
        window.remove_selected()
        window.clear_queue()
        window.choose_files()
        window.paste_files()  # The fixture rejects any unintended clipboard read.
        type(window).add_paths(window, [str(media[2])])
        assert not drag_enter(window, file_mime([media[2]])).isAccepted()
        assert [entry.path for entry in window.entries] == [media[0]]
        assert window.runner.calls == []
    finally:
        held.release.set()
        _wait_for_import(window, qt_app)
    assert [entry.path for entry in window.entries] == media[:2]
    assert all(control.isEnabled() for control in controls)


def test_close_during_folder_scan_waits_for_thread_and_discards_cancelled_import(window, tmp_path, monkeypatch, qt_app):
    folder = tmp_path / 'folder'
    folder.mkdir()
    (folder / 'sample.wav').write_bytes(b'synthetic media')
    held = _hold_import_scan(monkeypatch)
    type(window).add_paths(window, [str(folder)])
    try:
        assert held.entered.wait(2)
        log_before = window.event_log.editor.toPlainText()
        assert not window.close()
        assert held.cancellation_seen.wait(2)
        assert window.closing and window.importer.active
        assert window.isVisible() and not window.isEnabled()
        assert window.runner.cancel_count == 0
    finally:
        held.release.set()
        _wait_for_import(window, qt_app)
    qt_app.processEvents()
    assert not window.isVisible() and not window.clock.isActive()
    assert window.entries == [] and window.runner.calls == []
    assert window.event_log.editor.toPlainText() == log_before


def test_queue_uses_full_left_column_when_activity_log_collapses(window, media, qt_app):
    window.add_paths([str(path) for path in media])
    qt_app.processEvents()
    queue_before = window.queue_card.geometry()
    side_before = window.side_panel.geometry()
    assert queue_before.right() < side_before.left()
    assert abs(queue_before.top() - side_before.top()) <= 1
    assert abs(queue_before.bottom() - side_before.bottom()) <= 1
    assert queue_before.height() > window.status_card.height() * 2
    expanded_log_height = window.event_log.height()
    window.event_log.collapse_button.click()
    qt_app.processEvents()
    assert not window.event_log.editor.isVisible()
    assert window.event_log.height() < expanded_log_height
    assert window.queue_card.geometry() == queue_before
    assert window.side_panel.geometry() == side_before
    window.event_log.collapse_button.click()
    qt_app.processEvents()
    assert window.event_log.editor.isVisible()
    assert window.queue_card.geometry() == queue_before


def test_numbered_output_warning_and_output_path_have_distinct_log_colors(window, media):
    from core.file_gui.log_view import LEVEL_COLORS
    from core.i18n import tr

    window.add_paths([str(media[0])])
    window.start_batch()
    output = str(media[0].with_name('first clip (2).txt'))
    window.runner.progress.emit({'type': 'completed', 'processed_seconds': 2,
                                 'total_seconds': 2, 'elapsed_seconds': 1, 'sequence': 2})
    window.runner.outputs.emit([output])
    window.runner.complete()
    document = window.event_log.editor.document()
    for message, level in ((tr('files.log_numbered', sequence=2), 'warning'),
                           (tr('files.log_output', path=output), 'output')):
        cursor = document.find(message)
        assert not cursor.isNull()
        assert cursor.charFormat().foreground().color().name() == LEVEL_COLORS[level]


def select_rows(window, rows):
    window.file_list.clearSelection()
    for row in rows:
        window.file_list.item(row).setSelected(True)


def test_reorder_preserves_entries_selection_and_actual_execution_order(window, media):
    window.add_paths(list(map(str, media)))
    entries = list(window.entries)
    entries[2].metrics['fixture'] = 123
    select_rows(window, [1, 2])
    window.move_rows([1, 2], 0)
    assert window.entries == [entries[1], entries[2], entries[0]]
    assert window.selected_rows() == [0, 1]
    assert window.entries[1].metrics['fixture'] == 123
    window.start_batch()
    while window.runner.active:
        window.runner.complete()
    assert [path for path, _ in window.runner.calls] == [media[1], media[2], media[0]]


def test_noncontiguous_reorder_and_keyboard_delete_only_remove_queue_items(window, media):
    from PySide6.QtTest import QTest

    window.add_paths(list(map(str, media)))
    select_rows(window, [0, 2])
    window.move_rows([0, 2], 3)
    assert [entry.path for entry in window.entries] == [media[1], media[0], media[2]]
    assert window.selected_rows() == [1, 2]
    QTest.keyClick(window.file_list, Qt.Key.Key_Delete)
    assert [entry.path for entry in window.entries] == [media[1]]
    assert all(path.exists() for path in media)


def test_keyboard_reorder_and_name_sort_keep_selection_on_same_files(window, media):
    from PySide6.QtTest import QTest

    window.add_paths(list(map(str, reversed(media))))
    select_rows(window, [1, 2])
    QTest.keyClick(window.file_list, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert [entry.path for entry in window.entries] == [media[1], media[0], media[2]]
    QTest.keyClick(window.file_list, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert [entry.path for entry in window.entries] == list(reversed(media))
    window.sort_queue()
    assert [entry.path for entry in window.entries] == media
    assert window.selected_rows() == [0, 1]
    window.sort_queue(True)
    assert [entry.path for entry in window.entries] == list(reversed(media))
    assert window.selected_rows() == [1, 2]


def test_context_menu_actions_are_guarded_during_processing(window, media):
    from core.i18n import tr

    window.add_paths(list(map(str, media)))
    select_rows(window, [1])
    menu = window.build_queue_menu()
    actions = {action.text(): action for action in menu.actions()}
    actions[tr('files.move_top')].trigger()
    assert window.entries[0].path == media[1]
    window.start_batch()
    expected = list(window.entries)
    window.move_rows([0], 3)
    window.move_selected(1)
    window.sort_queue(True)
    window.remove_selected()
    assert window.entries == expected and not window.file_list.dragEnabled()
    locked = window.build_queue_menu()
    actions = {action.data(): action for action in locked.actions()}
    for key in ('move_top', 'move_bottom', 'move_up', 'move_down', 'sort_ascending', 'sort_descending', 'remove'):
        assert not actions[key].isEnabled()
    assert actions['open_folder'].isEnabled()
    menu.deleteLater()
    locked.deleteLater()


def test_refresh_retains_multiple_selection_and_current_file(window, media):
    window.add_paths(list(map(str, media[:2])))
    window.file_list.setCurrentRow(1)
    select_rows(window, [0, 1])
    window.add_paths([str(media[2])])
    assert window.selected_rows() == [0, 1]
    assert window.file_list.currentRow() == 1


def test_internal_drop_moves_rows_and_external_drop_still_imports(window, media):
    window.add_paths(list(map(str, media[:2])))
    select_rows(window, [0])
    event = SimpleNamespace(
        source=lambda: window.file_list,
        position=lambda: QPointF(10, window.file_list.viewport().height() - 2),
        setDropAction=lambda action: None,
        accept=lambda: None,
    )
    window.file_list.dropEvent(event)
    assert [entry.path for entry in window.entries] == [media[1], media[0]]
    mime = file_mime([media[2]])
    assert drag_enter(window.file_list.viewport(), mime).isAccepted()
    assert drop(window.file_list.viewport(), mime).isAccepted()
    assert [entry.path for entry in window.entries] == [media[1], media[0], media[2]]


@pytest.mark.parametrize('apply', [False, True])
def test_restore_defaults_is_staged_until_apply(qt_app, apply):
    from core.file_gui.options import DEFAULTS
    from core.file_gui.settings import FileOptionsDialog

    original = dict(DEFAULTS, language='english', file_seg_duration=30, file_result_timeout=200)
    dialog = FileOptionsDialog(original, 'ws://127.0.0.1:6016')
    dialog.restore_button.click()
    assert dialog.values == original
    assert dialog.fields['file_seg_duration'].value() == 60
    assert dialog.fields['file_seg_overlap'].value() == 4
    if apply:
        dialog.accept()
        assert dialog.result() and dialog.values == DEFAULTS
    else:
        dialog.reject()
        assert not dialog.result() and dialog.values == original
    assert original['language'] == 'english' and original['file_seg_duration'] == 30
    dialog.close()


def test_number_stepper_buttons_limits_and_keyboard_input(qt_app):
    from PySide6.QtTest import QTest
    from core.file_gui.options import DEFAULTS
    from core.file_gui.settings import FileOptionsDialog

    dialog = FileOptionsDialog(DEFAULTS, 'ws://127.0.0.1:6016')
    control = dialog.fields['file_max_inflight_chunks']
    stepper = dialog.steppers['file_max_inflight_chunks']
    stepper.increase.click()
    assert control.value() == 5 and not dialog.result()
    stepper.decrease.click()
    assert control.value() == 4
    control.setValue(control.minimum())
    assert not stepper.decrease.isEnabled()
    control.setValue(control.maximum())
    assert not stepper.increase.isEnabled()
    control.selectAll()
    QTest.keyClicks(control, '8')
    control.interpretText()
    assert control.value() == 8
    dialog.restore_button.click()
    assert control.value() == 4 and stepper.increase.isEnabled() and stepper.decrease.isEnabled()
    dialog.close()


def test_row_clicks_support_ctrl_shift_and_drag_preview(window, media, qt_app, monkeypatch):
    from PySide6.QtTest import QTest
    from core.file_gui import queue_view

    window.add_paths(list(map(str, media)))
    qt_app.processEvents()
    view = window.file_list
    for row, modifier in ((0, Qt.KeyboardModifier.NoModifier), (2, Qt.KeyboardModifier.ControlModifier)):
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, modifier,
                         view.visualItemRect(view.item(row)).center())
    assert window.selected_rows() == [0, 2]
    calls = []

    class Drag:
        def __init__(self, source):
            assert source is view

        def setMimeData(self, mime):
            assert mime.hasFormat('application/x-qabstractitemmodeldatalist')

        def setPixmap(self, pixmap):
            assert not pixmap.isNull()

        def setHotSpot(self, point):
            assert point.y() >= 0

        def exec(self, actions):
            calls.append(actions)

        def deleteLater(self):
            pass

    monkeypatch.setattr(queue_view, 'QDrag', Drag)
    view.startDrag(Qt.DropAction.MoveAction)
    assert calls == [Qt.DropAction.MoveAction]
    assert [entry.path for entry in window.entries] == media
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     view.visualItemRect(view.item(0)).center())
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                     view.visualItemRect(view.item(2)).center())
    assert window.selected_rows() == [0, 1, 2]


def test_pause_after_file_saves_result_and_resumes_reordered_remaining_files(window, media):
    from core.i18n import tr

    window.add_paths(list(map(str, media)))
    window.start_batch()
    window.pause_button.click()
    assert window.pause_requested and window.busy and not window.paused
    assert window.runner.cancel_count == 0
    output = media[0].with_suffix('.srt')
    window.runner.outputs.emit([str(output)])
    window.runner.complete()
    assert window.paused and not window.busy and len(window.runner.calls) == 1
    assert window.entries[0].outputs == (output,)
    assert window.start_button.text() == tr('files.resume')
    assert window.file_list.dragEnabled()
    window.move_rows([2], 0)
    assert [entry.path for entry in window.entries] == [media[0], media[2], media[1]]
    assert not (window.file_list.item(0).flags() & Qt.ItemFlag.ItemIsDragEnabled)
    window.start_button.click()
    assert not window.paused and not window.pause_requested
    window.runner.complete()
    window.runner.complete()
    assert [path for path, _ in window.runner.calls] == [media[0], media[2], media[1]]
    assert all(entry.state == 'completed' for entry in window.entries)


@pytest.mark.parametrize('operation', ['up', 'down', 'sort', 'drag_completed'])
def test_paused_queue_movement_never_repositions_completed_entries(window, media, operation):
    window.add_paths(list(map(str, reversed(media))))
    window.entries[1].state = 'completed'
    completed = window.entries[1]
    window.paused = True
    window.refresh_queue()
    if operation == 'up':
        select_rows(window, [2])
        window.move_selected(-1)
    elif operation == 'down':
        select_rows(window, [0])
        window.move_selected(1)
    elif operation == 'sort':
        window.sort_queue()
    else:
        window.move_rows([1], 0)
    assert window.entries[1] is completed
    if operation != 'drag_completed':
        assert [entry.path for entry in window.entries] == media


def test_pause_request_can_be_revoked_and_last_file_finishes_without_stale_pause(window, media):
    window.add_paths(list(map(str, media[:2])))
    window.start_batch()
    window.pause_button.click()
    window.pause_button.click()
    assert not window.pause_requested
    window.runner.complete()
    assert window.current.path == media[1] and window.busy
    window.pause_button.click()
    window.runner.complete()
    assert not window.paused and not window.pause_requested and not window.busy


@pytest.mark.parametrize('accept', [False, True])
def test_abort_confirmation_defaults_to_keep_processing_and_discards_only_after_confirm(window, media, monkeypatch, accept):
    window.add_paths(list(map(str, media)))
    window.start_batch()
    window.runner.complete()
    window.runner.progress.emit({'type': 'progress', 'processed_seconds': 30, 'total_seconds': 60})

    def answer(dialog):
        assert dialog.defaultButton() is dialog.button(QMessageBox.StandardButton.No)
        assert dialog.escapeButton() is dialog.button(QMessageBox.StandardButton.No)
        assert dialog.textFormat() == Qt.TextFormat.PlainText
        assert media[1].name in dialog.text()
        assert window.confirming_abort and window.runner.cancel_count == 0
        return QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, 'exec', answer)
    window.stop_button.click()
    assert window.runner.cancel_count == int(accept)
    if not accept:
        assert window.busy and not window.stopping and window.current.metrics['processed_seconds'] == 30
        return
    assert window.busy and window.stopping and not window.paused
    window.runner.complete(False, 'cancelled')
    assert window.paused and not window.busy
    assert window.entries[1].state == 'cancelled' and window.entries[1].metrics == {}
    window.move_rows([2], 1)
    window.start_button.click()
    window.runner.complete()
    assert window.current.path == media[1] and window.current.metrics['processed_seconds'] == 0
    window.runner.complete()
    assert [path for path, _ in window.runner.calls] == [media[0], media[1], media[2], media[1]]


@pytest.mark.parametrize('accept', [False, True])
def test_file_finishing_during_abort_dialog_never_cancels_next_file(window, media, monkeypatch, accept):
    window.add_paths(list(map(str, media[:2])))
    window.start_batch()

    def answer(dialog):
        window.runner.complete()
        assert len(window.runner.calls) == 1 and window.current is None
        return QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, 'exec', answer)
    window.stop_button.click()
    assert window.runner.cancel_count == 0 and window.entries[0].state == 'completed'
    if accept:
        assert window.paused and not window.busy and len(window.runner.calls) == 1
    else:
        assert window.busy and window.current.path == media[1] and not window.paused


def test_abort_after_saved_result_waits_for_exit_instead_of_restarting_finished_file(window, media):
    window.add_paths(list(map(str, media[:2])))
    window.start_batch()
    window.runner.progress.emit({'type': 'completed', 'processed_seconds': 60, 'total_seconds': 60})
    window.stop_button.click()
    assert window.runner.cancel_count == 0 and window.pause_requested
    window.runner.complete()
    assert window.paused and window.entries[0].state == 'completed'


def test_pause_survives_file_failure_and_clearing_queue_resets_pause(window, media):
    window.add_paths(list(map(str, media[:2])))
    window.start_batch()
    window.pause_button.click()
    window.runner.complete(False, 'decode_failed')
    assert window.paused and not window.busy and window.entries[0].state == 'failed'
    window.clear_queue()
    assert not window.paused and not window.start_button.isEnabled()


def test_close_while_pause_is_requested_cancels_without_prompt_or_next_file(window, media, monkeypatch):
    window.add_paths(list(map(str, media)))
    window.start_batch()
    window.pause_button.click()
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: pytest.fail('Close must use existing cleanup'))
    window.close()
    assert window.closing and window.runner.cancel_count == 1
    window.runner.complete(False, 'cancelled')
    assert not window.busy and not window.paused and not window.isVisible()
    assert len(window.runner.calls) == 1


@pytest.mark.parametrize('seconds,blocks', [(20, 1), (61, 1), (67.9, 1), (68, 2), (120, 2), (128, 3), (140, 3)])
def test_chunk_plan_matches_server_overlap_threshold_and_final_tail(window, media, seconds, blocks):
    window.add_paths([str(media[0])])
    window.start_batch()
    window.runner.progress.emit({'type': 'progress', 'stage': 'transcribing', 'total_seconds': seconds,
                                 'processed_seconds': 0, 'chunks_completed': 0})
    assert window.progress.chunks_total == blocks
    assert window.progress.audio_seconds == seconds
    if blocks > 1:
        rectangles = window.progress.chunk_rects()
        assert rectangles[-1][1].width() / rectangles[0][1].width() == pytest.approx(
            (seconds - (blocks - 1) * 60) / 60)


def test_chunk_count_survives_skipped_gui_events_and_final_duration_correction(window, media):
    from core.i18n import tr

    window.add_paths([str(media[0])])
    window.start_batch()
    window.runner.progress.emit({'type': 'progress', 'stage': 'transcribing', 'total_seconds': 180,
                                 'processed_seconds': 120, 'chunks_completed': 2,
                                 'chunk_seconds': 60, 'chunk_elapsed_seconds': 12, 'speed': 5})
    assert window.progress.chunks_completed == 2
    assert window.chunk_metrics.text() == tr('files.speed_metrics', average='5.00×', last='5.00×')
    window.runner.progress.emit({'type': 'completed', 'processed_seconds': 120.00001, 'total_seconds': 120.00001})
    assert window.progress.chunks_completed == window.progress.chunks_total == 2
    assert not window.progress.chunk_active
    assert window.progress.audio_seconds == pytest.approx(120.00001)


def test_running_remove_and_clear_are_disabled_and_programmatic_calls_do_nothing(window, media):
    window.add_paths(list(map(str, media)))
    window.start_batch()
    original = list(window.entries)
    assert not window.clear_button.isEnabled()
    window.remove_selected()
    window.clear_queue()
    assert window.entries == original and window.runner.cancel_count == 0
    window.pause_button.click()
    window.runner.complete()
    assert window.paused and window.clear_button.isEnabled()


def test_check_mode_shares_selection_and_preserves_empty_checks_across_import(window, media, qt_app):
    from PySide6.QtWidgets import QCheckBox
    from PySide6.QtTest import QTest
    from core.i18n import tr

    window.add_paths(list(map(str, media[:2])))
    window.select_button.click()
    assert window.detail.isHidden()
    qt_app.processEvents()
    assert window.file_list.check_mode and not window.file_list.dragEnabled()
    window.file_list.clearSelection()
    window.add_paths([str(media[2])])
    assert not window.file_list.selectedItems()
    checks = [window.file_list.itemWidget(window.file_list.item(i)).findChild(QCheckBox, 'queueCheck')
              for i in range(3)]
    assert all(check.isVisible() and not check.isChecked() for check in checks)
    # Accessibility actions and list gestures both use the same selection.
    checks[0].click()
    QTest.mouseClick(window.file_list.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.file_list.check_rect(window.file_list.item(2)).center())
    assert window.selected_rows() == [0, 2]
    assert checks[0].isChecked() and checks[2].isChecked()
    assert window.count.text() == tr('files.selection_count', selected=2, total=3)
    removal = next(action for action in window.build_queue_menu().actions() if action.data() == 'remove')
    removal.trigger()
    assert [entry.path for entry in window.entries] == [media[1]]
    assert all(path.is_file() for path in media)
    assert not window.file_list.selectedItems()
    window.select_button.click()
    assert not window.file_list.check_mode and window.file_list.dragEnabled()


def test_checked_selection_survives_reorder_and_does_not_limit_transcription(window, media):
    window.add_paths(list(map(str, media)))
    window.select_button.click()
    select_rows(window, [2])
    selected_path = media[2]
    window.move_rows([2], 0)
    assert [item.data(Qt.ItemDataRole.UserRole) for item in window.file_list.selectedItems()] == [str(selected_path)]
    window.start_batch()
    assert not next(action for action in window.build_queue_menu().actions() if action.data() == 'remove').isEnabled()
    assert len(window.batch_entries) == 3
    window.remove_selected()
    assert len(window.entries) == 3


@pytest.mark.parametrize('accept', [False, True])
def test_clear_queue_confirms_count_defaults_to_cancel_and_preserves_disk(window, media, monkeypatch, accept):
    from core.i18n import tr
    window.add_paths(list(map(str, media)))
    assert not hasattr(window, 'remove_button')
    assert window.clear_button.objectName() == 'queueClear'

    def confirm(dialog):
        assert dialog.text() == tr('files.clear_queue_body', count=3)
        assert dialog.standardButton(dialog.defaultButton()) == QMessageBox.StandardButton.No
        assert dialog.standardButton(dialog.escapeButton()) == QMessageBox.StandardButton.No
        assert len(window.entries) == 3
        return QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, 'exec', confirm)
    window.clear_button.click()
    assert len(window.entries) == (0 if accept else 3)
    assert all(path.is_file() for path in media)


def test_clear_confirmation_cannot_clear_a_queue_that_changed_while_open(window, media, monkeypatch):
    from core.file_gui.window import QueueEntry
    window.add_paths(list(map(str, media[:2])))

    def confirm(dialog):
        window.entries.append(QueueEntry(media[2]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, 'exec', confirm)
    window.clear_queue()
    assert [entry.path for entry in window.entries] == media


@pytest.mark.parametrize('rows,direction,expected', [
    ([1, 3], -1, 'BADCE'), ([1, 3], 1, 'ACBED'),
    ([0, 3], -1, 'ABDCE'), ([1, 4], 1, 'ACBDE'),
    ([1, 2], -1, 'BCADE'), ([1, 2], 1, 'ADBCE'),
])
def test_multiselect_menu_moves_each_group_and_keeps_boundary_groups(window, media, rows, direction, expected):
    from core.file_gui.window import QueueEntry
    from core.i18n import tr
    window.entries = [QueueEntry(media[0].parent / f'{name}.wav') for name in 'ABCDE']
    window.refresh_queue()
    select_rows(window, rows)
    chosen = {window.entries[i].path for i in rows}
    menu = window.build_queue_menu()
    key = 'move_up' if direction == -1 else 'move_down'
    action = next(action for action in menu.actions() if action.data() == key)
    assert action.isEnabled()
    assert action.text() == tr('files.' + key + '_count', count=2)
    action.trigger()
    assert ''.join(entry.path.stem for entry in window.entries) == expected
    assert {window.entries[i].path for i in window.selected_rows()} == chosen
    menu.deleteLater()


def test_counted_context_removal_targets_the_files_named_when_menu_opened(window, media):
    from core.i18n import tr
    window.add_paths(list(map(str, media)))
    select_rows(window, [0, 2])
    menu = window.build_queue_menu()
    removal = next(action for action in menu.actions() if action.data() == 'remove')
    assert removal.text() == tr('files.remove_count', count=2)
    assert removal.property('destructive')
    select_rows(window, [1])
    removal.trigger()
    assert [entry.path for entry in window.entries] == [media[1]]
    menu.deleteLater()
