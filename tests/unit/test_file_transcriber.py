import asyncio
from pathlib import Path

from core.client.transcribe.file_transcriber import (
    ProgressEstimator,
    TranscriptionSummary,
    format_duration,
    read_fixed_chunk,
)


class FragmentedReader:
    """模拟 FFmpeg 管道每次只交付少量可用字节。"""

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
            assert "必须为正数" in str(exc)
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
