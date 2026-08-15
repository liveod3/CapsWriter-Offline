import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from pathlib import Path

from core.client.manager.file_runner import FileRunner, resolve_input_paths


class NonInteractiveStdin:
    def isatty(self) -> bool:
        return False


def test_file_runner_does_not_read_input_without_interactive_stdin():
    app = SimpleNamespace(
        state=SimpleNamespace(),
        ws=SimpleNamespace(),
        hotword=SimpleNamespace(start=lambda: None, stop=lambda: None),
    )
    runner = FileRunner(app, [], output_formats=frozenset({'txt'}))

    with (
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
