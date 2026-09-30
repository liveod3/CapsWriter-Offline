"""Exercise bounded file discovery without user media or desktop interaction."""

from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace

import pytest

from core.file_gui import imports


EXTENSIONS = frozenset({'.wav', '.mp4'})


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'synthetic fixture')
    return path


def scan(values, **options):
    return imports.scan_paths(values, options.pop('existing', ()), EXTENSIONS, **options)


def test_directory_import_filters_outputs_and_orders_nested_media(tmp_path):
    first = touch(tmp_path / 'A.wav')
    nested = touch(tmp_path / 'clips' / 'clip.MP4')
    last = touch(tmp_path / 'z.wav')
    touch(tmp_path / 'A.txt')
    touch(tmp_path / 'clips' / 'clip.json')
    result = scan([tmp_path])
    assert result.paths == (first, nested, last)
    assert result.skipped == 2
    assert result.errors == 0 and not result.limited and not result.cancelled


def test_nonrecursive_import_keeps_immediate_files_and_explicit_other_folders(tmp_path):
    immediate = touch(tmp_path / 'root.wav')
    nested = touch(tmp_path / 'nested' / 'child.wav')
    assert scan([tmp_path], recursive=False).paths == (immediate,)
    assert scan([tmp_path, nested.parent], recursive=False).paths == (immediate, nested)


def test_resolved_casefold_deduplication_includes_existing_queue_and_overlapping_inputs(tmp_path):
    first = touch(tmp_path / 'first.wav')
    second = touch(tmp_path / 'sub' / 'second.wav')
    result = scan([tmp_path, second, second.parent, tmp_path], existing=[str(first).upper()])
    assert result.paths == (second,)
    assert result.skipped == 4


def test_capacity_includes_existing_entries_and_stops_before_later_inputs(tmp_path, monkeypatch):
    paths = [touch(tmp_path / f'{index}.wav') for index in range(5)]
    original = Path.lstat
    inspected = []

    def observed(path):
        inspected.append(path)
        return original(path)

    monkeypatch.setattr(Path, 'lstat', observed)
    result = scan(paths[1:], existing=[paths[0]], capacity=3)
    assert result.paths == tuple(paths[1:3]) and result.limited
    assert paths[3] not in inspected
    assert scan(paths, capacity=0).paths == ()


def test_oversized_directory_is_bounded_and_omitted_without_unstable_subset(tmp_path, monkeypatch):
    for index in range(10):
        touch(tmp_path / f'{index}.wav')
    original = imports.os.scandir
    enumerated = []

    class CountedEntries:
        def __enter__(self):
            self.entries = original(tmp_path)
            return self

        def __exit__(self, *_):
            self.entries.close()

        def __iter__(self):
            for entry in self.entries:
                enumerated.append(entry.name)
                yield entry

    monkeypatch.setattr(imports.os, 'scandir', lambda _: CountedEntries())
    result = scan([tmp_path], max_entries=3)
    assert result.limited and not result.paths
    assert len(enumerated) == 4  # One sentinel proves that the directory exceeds the bound.


def test_depth_bound_keeps_shallow_files_and_reports_partial_import(tmp_path):
    first = touch(tmp_path / 'first.wav')
    touch(tmp_path / 'sub' / 'nested' / 'last.wav')
    result = scan([tmp_path], max_depth=1)
    assert result.paths == (first,) and result.limited and result.skipped == 1


@pytest.mark.parametrize('mode,attributes', [
    (stat.S_IFLNK, 0),
    (stat.S_IFDIR, stat.FILE_ATTRIBUTE_REPARSE_POINT),
])
def test_directory_symlinks_and_windows_junctions_are_never_traversed(tmp_path, monkeypatch, mode, attributes):
    linked = tmp_path / 'linked'
    touch(linked / 'cycle.wav')
    first = touch(tmp_path / 'first.wav')
    original = Path.lstat

    def metadata(path):
        if path == linked:
            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
        return original(path)

    monkeypatch.setattr(Path, 'lstat', metadata)
    result = scan([tmp_path, linked])
    assert result.paths == (first,) and result.skipped == 2
    assert not result.errors


def test_inaccessible_paths_produce_counts_without_exception_details(tmp_path, monkeypatch):
    denied = tmp_path / 'denied'
    denied.mkdir()
    good = touch(tmp_path / 'good.wav')
    original = imports.os.scandir

    def access(path):
        if path == denied:
            raise PermissionError('PRIVATE FILESYSTEM DETAIL')
        return original(path)

    monkeypatch.setattr(imports.os, 'scandir', access)
    result = scan([denied, tmp_path / 'missing.wav', good, 'relative.wav'])
    assert result.paths == (good,) and result.errors == 2 and result.skipped == 3
    assert 'PRIVATE' not in repr(result)


def test_cancellation_checks_while_enumerating_and_discards_incomplete_directory(tmp_path, monkeypatch):
    for index in range(10):
        touch(tmp_path / f'{index}.wav')
    cancellation = threading.Event()
    original = imports.os.scandir

    class CancelledEntries:
        def __enter__(self):
            self.entries = original(tmp_path)
            return self

        def __exit__(self, *_):
            self.entries.close()

        def __iter__(self):
            for index, entry in enumerate(self.entries):
                if index == 2:
                    cancellation.set()
                yield entry

    monkeypatch.setattr(imports.os, 'scandir', lambda _: CancelledEntries())
    result = scan([tmp_path], cancelled=cancellation.is_set)
    assert result.cancelled and not result.paths
    assert scan([tmp_path], cancelled=lambda: True) == imports.ImportResult(cancelled=True)


def test_input_iterators_are_bounded_even_when_all_paths_are_invalid():
    def unbounded():
        while True:
            yield 'relative.wav'

    result = scan(unbounded())
    assert result.limited and result.skipped == imports.MAX_FILES


def test_worker_scans_off_thread_and_finishes_only_after_cancellation_stops_thread(monkeypatch):
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    application = QApplication.instance() or QApplication([])
    worker = imports.FileImportWorker()
    entered = threading.Event()
    results = []
    thread_states = []

    def controlled_scan(**options):
        assert QThread.currentThread() != application.thread()
        entered.set()
        while not options['cancelled']():
            time.sleep(0.002)
        return imports.ImportResult(cancelled=True)

    monkeypatch.setattr(imports, 'scan_paths', controlled_scan)
    worker.finished.connect(results.append)
    worker.finished.connect(lambda _: thread_states.append((worker.active, thread.isRunning())))
    assert worker.start([], [], EXTENSIONS)
    thread = worker._thread
    try:
        assert entered.wait(2)
        assert worker.active and not worker.start([], [], EXTENSIONS)
        worker.cancel()
        deadline = time.monotonic() + 2
        while worker.active and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.002)
        assert results == [imports.ImportResult(cancelled=True)]
        assert thread_states == [(False, False)]
    finally:
        worker.cancel()
        if worker.active:
            thread.wait(2000)
        application.processEvents()
