"""Keep terminal and GUI progress identical without inventing audio positions."""

from pathlib import Path
import subprocess
import sys

import pytest

from core.file_progress import ProgressEstimator, ProgressSnapshot, format_duration


@pytest.mark.parametrize('seconds, expected', [
    (-1.0, '00:00'),
    (0.49, '00:00'),
    (0.5, '00:01'),
    (59.49, '00:59'),
    (59.5, '01:00'),
    (3599.5, '1:00:00'),
])
def test_shared_clock_keeps_terminal_rounding(seconds, expected):
    assert format_duration(seconds) == expected


def test_gui_refresh_matches_terminal_between_irregular_server_updates():
    estimator = ProgressEstimator(started_at=100.0)
    for processed, observed_at, next_refresh in ((60, 110, 115), (120, 123, 128.5)):
        estimator.update(processed, 180, now=observed_at)
        source_snapshot = estimator.snapshot(180, now=observed_at)
        event = {'type': 'progress', 'stage': 'transcribing', **source_snapshot.as_metrics()}
        gui_snapshot = ProgressSnapshot.from_event(event).advance(next_refresh - observed_at)
        terminal_snapshot = estimator.snapshot(180, now=next_refresh)

        assert gui_snapshot == terminal_snapshot
        assert gui_snapshot.processed_seconds == processed
        assert gui_snapshot.percentage == source_snapshot.percentage
        assert gui_snapshot.remaining_seconds == 180 - processed
        assert gui_snapshot.speed == estimator.live_speed(now=next_refresh)
        assert gui_snapshot.eta_seconds == estimator.eta_seconds(now=next_refresh)


def test_legacy_snapshot_without_observed_speed_retains_cumulative_fallback():
    snapshot = ProgressSnapshot(60, 180, 10, 20)
    refreshed = snapshot.advance(25)
    assert refreshed.processed_seconds == 60
    assert refreshed.percentage == pytest.approx(100 / 3)
    assert refreshed.remaining_seconds == 120
    assert refreshed.elapsed_seconds == 35
    assert refreshed.speed == pytest.approx(60 / 35)
    assert refreshed.rtf == pytest.approx(35 / 60)
    assert refreshed.eta_seconds == 0
    assert snapshot.elapsed_seconds == 10


def test_before_first_result_does_not_predict_processing_or_eta():
    snapshot = ProgressSnapshot(0, 180, 0, None).advance(12.5)
    assert snapshot.processed_seconds == snapshot.percentage == snapshot.speed == 0
    assert snapshot.elapsed_seconds == 12.5
    assert snapshot.eta_seconds is snapshot.rtf is None
    assert snapshot.remaining_seconds == 180


def test_unknown_media_duration_keeps_percent_and_remaining_unknown():
    estimator = ProgressEstimator(started_at=100)
    estimator.update(20, None, now=110)
    snapshot = estimator.snapshot(now=110).advance(5)
    assert snapshot.processed_seconds == 20
    assert snapshot.total_seconds is snapshot.percentage is snapshot.remaining_seconds is None
    assert snapshot.eta_seconds is None
    assert snapshot.speed == 2


def test_underestimated_known_duration_matches_terminal_expansion():
    snapshot = ProgressSnapshot.from_event({
        'processed_seconds': 120, 'total_seconds': 100, 'elapsed_seconds': 30,
    })
    assert snapshot.total_seconds == 120
    assert snapshot.percentage == 100
    assert snapshot.remaining_seconds == 0
    assert snapshot.processed_seconds == 120


def test_confirmed_audio_completion_is_not_artificially_capped():
    snapshot = ProgressSnapshot(120, 120, 30, 0)
    assert snapshot.percentage == 100
    assert snapshot.speed == 4
    assert snapshot.rtf == 0.25


def test_observed_speed_stays_stable_between_real_recognition_results():
    estimator = ProgressEstimator(started_at=100)
    estimator.update(60, 180, now=110)
    measured = estimator.snapshot(180, now=110)
    received = ProgressSnapshot.from_event(measured.as_metrics())
    for delay in (1, 5, 10, 25):
        terminal = estimator.snapshot(180, now=110 + delay)
        gui = received.advance(delay)
        assert terminal == gui
        assert terminal.speed == 6
        assert terminal.processed_seconds == 60
        assert terminal.percentage == measured.percentage
        assert terminal.elapsed_seconds == 10 + delay
        assert terminal.eta_seconds == max(0, 20 - delay)


def test_chunk_average_changes_only_on_actual_confirmed_audio_advances():
    estimator = ProgressEstimator(started_at=100)
    estimator.update(60, 180, now=110)
    deadline = estimator.eta_deadline
    for position, now in ((60, 115), (50, 119), (0, 125)):
        estimator.update(position, 180, now=now)
        assert estimator.live_speed(now=now) == 6
        assert estimator.completed == 60
        assert estimator.last_updated_at == 110
        assert estimator.eta_deadline == deadline

    estimator.update(120, 180, now=130)
    # Two chunks contain 120 seconds of audio completed in 10 + 20 seconds.
    assert estimator.live_speed(now=130) == pytest.approx(4)
    assert estimator.live_speed(now=135) == pytest.approx(4)
    assert estimator.chunks_completed == 2
    assert estimator.chunk_seconds == 60 and estimator.chunk_elapsed_seconds == 20


def test_total_learned_after_same_position_can_create_eta_without_new_speed():
    estimator = ProgressEstimator(started_at=100)
    estimator.update(20, None, now=110)
    estimator.update(20, 20, now=112)
    assert estimator.live_speed(now=112) == 2
    assert estimator.eta_seconds(now=112) == 0


def test_completed_event_keeps_actual_cumulative_summary_speed():
    event = {'type': 'completed', 'processed_seconds': 120, 'total_seconds': 120,
             'elapsed_seconds': 30, 'speed': 4.0, 'eta_seconds': 0}
    snapshot = ProgressSnapshot.from_event(event)
    assert snapshot.speed == 4
    assert snapshot.rtf == 0.25


def test_shared_progress_import_does_not_load_client_config_or_audio():
    code = (
        'import sys; from core.file_progress import ProgressEstimator; '
        'assert not ({"config_client", "core.client", "numpy", "sounddevice", '
        '"PySide6"} & sys.modules.keys())'
    )
    result = subprocess.run(
        [sys.executable, '-c', code], cwd=Path(__file__).parents[2],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_original_imports_reexport_the_same_shared_implementations():
    from core.client.transcribe import file_transcriber
    assert file_transcriber.ProgressEstimator is ProgressEstimator
    assert file_transcriber.format_duration is format_duration


def test_unequal_chunk_lengths_use_audio_over_elapsed_not_mean_of_ratios():
    estimator = ProgressEstimator(started_at=0)
    estimator.update(60, 140, now=10)
    estimator.update(120, 140, now=30)
    estimator.update(140, 140, now=35)
    snapshot = estimator.snapshot(140, now=35)
    assert snapshot.chunks_completed == 3
    assert snapshot.chunk_seconds == 20 and snapshot.chunk_elapsed_seconds == 5
    assert snapshot.speed == 4  # 140 / (10 + 20 + 5), not (6 + 3 + 4) / 3.
    assert snapshot.advance(50).speed == 4
