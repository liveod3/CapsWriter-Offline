from types import SimpleNamespace
from unittest.mock import patch

from core.server.worker.gpu_monitor import (
    GpuPressureDetector,
    GpuSample,
    highest_pressure,
    parse_nvidia_smi_output,
)
from core.server.worker.process_manager import ProcessManager


def test_parse_nvidia_smi_output_skips_unsupported_rows():
    samples = parse_nvidia_smi_output(
        '4300, 6144, 55\n'
        'N/A, N/A, N/A\n'
        '1024, 8192, 20\n'
    )

    assert samples == [
        GpuSample(4300, 6144, 55),
        GpuSample(1024, 8192, 20),
    ]


def test_pressure_detector_requires_sustained_pressure_and_reports_once():
    detector = GpuPressureDetector(threshold=0.9, consecutive_samples=3)
    high = GpuSample(950, 1000, 40)

    assert detector.observe(high) is False
    assert detector.observe(high) is False
    assert detector.observe(high) is True
    assert detector.observe(high) is False


def test_pressure_detector_can_report_again_after_recovery():
    detector = GpuPressureDetector(threshold=0.9, consecutive_samples=1)

    assert detector.observe(GpuSample(950, 1000, 30)) is True
    assert detector.observe(GpuSample(800, 1000, 0)) is False
    assert detector.observe(GpuSample(950, 1000, 30)) is True


def test_highest_pressure_uses_ratio_and_accepts_empty_input():
    assert highest_pressure([]) is None
    assert highest_pressure([
        GpuSample(4000, 8000, 80),
        GpuSample(3000, 4000, 40),
    ]) == GpuSample(3000, 4000, 40)


def test_repeated_aligner_idle_exits_emit_one_prominent_warning():
    manager = ProcessManager(SimpleNamespace())

    with (
        patch(
            'core.server.worker.process_manager.time.monotonic',
            side_effect=[1000, 1020, 1040, 1050],
        ),
        patch('core.server.worker.process_manager.console.print') as output,
    ):
        for _ in range(4):
            manager._record_aligner_idle_exit()

    output.assert_called_once()
