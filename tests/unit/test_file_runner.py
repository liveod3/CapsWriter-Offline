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


def test_file_batches_reuse_the_client_sink_without_extra_files(tmp_path, monkeypatch):
    logger = logging.getLogger('client')
    existing = tmp_path / 'client.jsonl'
    monkeypatch.setattr(logger, 'diagnostic_path', existing, raising=False)
    handlers = list(logger.handlers)
    first, second = TranscriptionTaskLog(tmp_path), TranscriptionTaskLog(tmp_path)
    assert first.start() == second.start() == existing
    assert first.batch_id != second.batch_id
    first.close()
    second.close()
    assert logger.handlers == handlers
    assert not (tmp_path / 'logs').exists()


def test_file_batch_cannot_override_disabled_diagnostic_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(logging.getLogger('client'), 'diagnostic_path', None, raising=False)
    task_log = TranscriptionTaskLog(tmp_path, enabled=True)
    assert task_log.start() is None
    task_log.close()
    assert not list(tmp_path.iterdir())
