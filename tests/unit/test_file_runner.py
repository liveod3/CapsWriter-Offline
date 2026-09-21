import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from pathlib import Path

from config_client import ClientConfig as Config
from core.client.manager.file_runner import (
    FileRunner,
    TranscriptionTaskLog,
    resolve_input_paths,
)


class NonInteractiveStdin:
    def isatty(self) -> bool:
        return False


def test_file_runner_does_not_read_input_without_interactive_stdin():
    app = SimpleNamespace(
        state=SimpleNamespace(),
        ws=SimpleNamespace(),
    )
    runner = FileRunner(app, [], output_formats=frozenset({'txt'}))

    with (
        patch.object(Config, 'file_separate_log', False, create=True),
        patch("core.client.manager.file_runner.sys.stdin", NonInteractiveStdin()),
        patch("builtins.input", side_effect=AssertionError("不应读取非交互 stdin")),
        patch("core.client.ui.TipsDisplay.show_file_tips"),
    ):
        asyncio.run(runner.run())


def test_resolve_input_paths_honors_recursive_override(tmp_path: Path) -> None:
    top_level = tmp_path / "top.mp4"
    nested_dir = tmp_path / "nested"
    nested = nested_dir / "nested.mp4"
    nested_dir.mkdir()
    top_level.touch()
    nested.touch()

    assert resolve_input_paths([tmp_path], recursive=False) == [top_level]
    assert resolve_input_paths([tmp_path], recursive=True) == [nested, top_level]


def test_failed_preflight_is_not_treated_as_a_completed_summary():
    class FailedTranscriber:
        def __init__(self, *_args, **_kwargs):
            pass

        async def check(self):
            return False

        async def close(self):
            pass

    app = SimpleNamespace(state=SimpleNamespace(), ws=SimpleNamespace())
    runner = FileRunner(app, [], output_formats=frozenset({'txt'}))

    with patch("core.client.transcribe.FileTranscriber", FailedTranscriber):
        result = asyncio.run(runner._process_file(Path("missing.wav")))

    assert result is None


def test_transcription_task_log_uses_year_and_month_directories(tmp_path: Path):
    task_log = TranscriptionTaskLog(tmp_path)
    client_logger = logging.getLogger('client')
    previous_level = client_logger.level
    client_logger.setLevel(logging.INFO)

    path = task_log.start(now=datetime(2026, 9, 2, 14, 5, 6))
    try:
        assert path == (
            tmp_path / 'logs' / 'transcribe' / '2026' / '09'
            / 'transcribe_20260902-140506.log'
        )
        logging.getLogger('client').info('独立日志测试')
    finally:
        task_log.close()
        client_logger.setLevel(previous_level)

    assert '独立日志测试' in path.read_text(encoding='utf-8')


def test_transcription_task_log_does_not_overwrite_same_second(tmp_path: Path):
    now = datetime(2026, 9, 2, 14, 5, 6)
    first = TranscriptionTaskLog(tmp_path)
    second = TranscriptionTaskLog(tmp_path)

    first_path = first.start(now=now)
    first.close()
    second_path = second.start(now=now)
    second.close()

    assert first_path.name == 'transcribe_20260902-140506.log'
    assert second_path.name == 'transcribe_20260902-140506 (2).log'


def test_transcription_task_log_falls_back_when_directory_is_unavailable(
    tmp_path: Path,
):
    task_log = TranscriptionTaskLog(tmp_path)

    with patch.object(Path, 'mkdir', side_effect=OSError('只读目录')):
        path = task_log.start(now=datetime(2026, 9, 2, 14, 5, 6))

    assert path == tmp_path / 'logs' / 'client_latest.log'
    task_log.close()
