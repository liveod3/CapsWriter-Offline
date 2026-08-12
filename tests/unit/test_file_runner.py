import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from core.client.manager.file_runner import FileRunner


class NonInteractiveStdin:
    def isatty(self) -> bool:
        return False


def test_file_runner_does_not_read_input_without_interactive_stdin():
    app = SimpleNamespace(
        state=SimpleNamespace(),
        ws=SimpleNamespace(),
        hotword=SimpleNamespace(start=lambda: None, stop=lambda: None),
    )
    runner = FileRunner(app, [])

    with (
        patch("core.client.manager.file_runner.sys.stdin", NonInteractiveStdin()),
        patch("builtins.input", side_effect=AssertionError("不应读取非交互 stdin")),
        patch("core.client.ui.TipsDisplay.show_file_tips"),
    ):
        asyncio.run(runner.run())
