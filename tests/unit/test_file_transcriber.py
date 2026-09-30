import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.client.transcribe.file_transcriber import (
    ProgressEstimator,
    TranscriptionSummary,
    format_duration,
    read_fixed_chunk,
    FileTranscriber,
)
from core.protocol import RecognitionMessage


class FragmentedReader:
    """Simulate an FFmpeg pipe delivering small partial reads."""

    def __init__(self, fragments: list[bytes]):
        self.fragments = [bytearray(fragment) for fragment in fragments]

    async def read(self, size: int) -> bytes:
        while self.fragments and not self.fragments[0]:
            self.fragments.pop(0)
        if not self.fragments:
            return b""

        fragment = self.fragments[0]
        result = bytes(fragment[:size])
        del fragment[:size]
        return result


def test_read_fixed_chunk_accumulates_fragmented_pipe_reads():
    async def run():
        reader = FragmentedReader([b"ab", b"c", b"de", b"f"])
        assert await read_fixed_chunk(reader, 5) == b"abcde"
        assert await read_fixed_chunk(reader, 5) == b"f"
        assert await read_fixed_chunk(reader, 5) == b""

    asyncio.run(run())


def test_read_fixed_chunk_rejects_invalid_size():
    async def run():
        reader = FragmentedReader([])
        try:
            await read_fixed_chunk(reader, 0)
        except ValueError as exc:
            assert "must be positive" in str(exc)
        else:
            raise AssertionError("无效分块大小应当被拒绝")

    asyncio.run(run())


def test_format_duration_uses_compact_clock_format():
    assert format_duration(0) == "00:00"
    assert format_duration(65.4) == "01:05"
    assert format_duration(3661) == "1:01:01"


def test_transcription_summary_calculates_speed_and_rtf():
    summary = TranscriptionSummary(
        audio_duration=120,
        elapsed=30,
        text_length=42,
        output_paths=(Path("result.txt"),),
        sequence=1,
    )

    assert summary.speed_ratio == 4
    assert summary.rtf == 0.25


def test_progress_estimator_smooths_speed_and_counts_eta_down():
    estimator = ProgressEstimator(started_at=100)
    estimator.update(20, 100, now=110)

    assert estimator.live_speed(now=110) == 2
    assert estimator.eta_seconds(now=110) == 40
    assert estimator.eta_seconds(now=115) == 35

    estimator.update(40, 100, now=120)
    assert estimator.live_speed(now=120) == 2
    assert estimator.eta_seconds(now=120) == 30


def test_progress_estimator_waits_for_known_total_before_eta():
    estimator = ProgressEstimator(started_at=10)
    estimator.update(5, None, now=12)

    assert estimator.eta_seconds(now=12) is None


def test_redirected_progress_uses_server_duration_without_fabricating_final(monkeypatch):
    events = []
    transcriber = FileTranscriber(
        SimpleNamespace(file_progress_callback=events.append), Path('synthetic.wav'),
        output_formats=frozenset({'txt'}),
    )
    monkeypatch.setattr('core.client.transcribe.file_transcriber.console',
                        SimpleNamespace(is_terminal=False))
    monkeypatch.setattr('core.client.transcribe.file_transcriber.time.perf_counter', lambda: 110.0)
    transcriber._started_at = 100.0
    transcriber._audio_duration = 100.0
    transcriber._decoded_duration = 100.0
    transcriber._start_progress()
    transcriber._update_progress(20.0)
    transcriber._update_progress(10.0)
    transcriber._update_progress(90.0, finished=True)

    assert transcriber._progress is None
    assert [event['processed_seconds'] for event in events] == [0, 20, 20, 90]
    assert events[1]['speed'] == 2.0
    assert events[1]['rtf'] == 0.5
    assert events[1]['eta_seconds'] == 40.0
    assert events[-1]['total_seconds'] == 100.0
    assert all(event['type'] == 'progress' for event in events)


def test_unknown_duration_keeps_percentage_and_eta_unavailable(monkeypatch):
    events = []
    transcriber = FileTranscriber(
        SimpleNamespace(file_progress_callback=events.append), Path('synthetic.wav'),
        output_formats=frozenset({'txt'}),
    )
    monkeypatch.setattr('core.client.transcribe.file_transcriber.console',
                        SimpleNamespace(is_terminal=False))
    transcriber._start_progress()
    transcriber._update_progress(10.0)
    assert events[-1]['processed_seconds'] == 10.0
    assert events[-1]['total_seconds'] is None
    assert events[-1]['eta_seconds'] is None


@pytest.mark.parametrize('connected', [False, True])
def test_progress_reports_preflight_and_connection_outcome(tmp_path, monkeypatch, connected):
    source = tmp_path / 'synthetic.wav'
    source.touch()
    events = []
    ws = SimpleNamespace(connect=AsyncMock(return_value=connected))
    app = SimpleNamespace(ws=ws, file_progress_callback=events.append)
    transcriber = FileTranscriber(app, source, output_formats=frozenset({'txt'}))
    monkeypatch.setattr('core.client.transcribe.file_transcriber.MediaTool.check_environment',
                        Mock(return_value=True))
    assert asyncio.run(transcriber.check()) is connected
    assert [event['stage'] for event in events] == ['checking', 'connecting']
    assert transcriber.failure_code == (None if connected else 'connection_failed')


def test_gui_connection_timeout_is_bounded_and_classified(tmp_path, monkeypatch):
    source = tmp_path / 'synthetic.wav'
    source.touch()

    async def connect(**_kwargs):
        await asyncio.Event().wait()

    app = SimpleNamespace(ws=SimpleNamespace(connect=connect), file_connect_timeout=0.01)
    transcriber = FileTranscriber(app, source, output_formats=frozenset({'txt'}))
    monkeypatch.setattr('core.client.transcribe.file_transcriber.MediaTool.check_environment',
                        Mock(return_value=True))
    assert asyncio.run(transcriber.check()) is False
    assert transcriber.failure_code == 'connection_failed'


def test_received_progress_filters_foreign_tasks_and_excludes_recognition_content(monkeypatch):
    async def run():
        events = []
        ws = SimpleNamespace(receive=AsyncMock())
        app = SimpleNamespace(ws=ws, file_progress_callback=events.append)
        transcriber = FileTranscriber(app, Path('synthetic.wav'), output_formats=frozenset({'txt'}))
        transcriber._send_complete.set()
        transcriber._audio_duration = 20.0
        ws.receive.side_effect = [
            RecognitionMessage('foreign-task', True, 900, 0, 0, 0, 'private fixture'),
            RecognitionMessage(transcriber.task_id, False, 10, 0, 0, 0, 'private fixture'),
            RecognitionMessage(transcriber.task_id, True, 20, 0, 0, 0, 'private fixture'),
        ]

        def save(*_args, **_kwargs):
            assert events[-1]['stage'] == 'saving'
            assert not any(event['type'] == 'completed' for event in events)
            return ('private fixture', 1, [Path('synthetic.txt')])

        monkeypatch.setattr('core.client.transcribe.file_transcriber.ResultHandler.save_results', save)
        assert await transcriber.receive() is True
        assert [event['processed_seconds'] for event in events] == [10, 20, 20]
        assert 'private fixture' not in repr(events)
        assert 'output_paths' not in repr(events)

    asyncio.run(run())


def test_broken_progress_observer_does_not_interrupt_transcription():
    transcriber = FileTranscriber(
        SimpleNamespace(file_progress_callback=Mock(side_effect=RuntimeError('observer closed'))),
        Path('synthetic.wav'), output_formats=frozenset({'txt'}),
    )
    transcriber._update_progress(5.0)
    assert transcriber._processed_duration == 5.0
