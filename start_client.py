# coding: utf-8
from __future__ import annotations

from typing import Sequence



def main(argv: Sequence[str] | None = None) -> int:
    """Parse client arguments before importing and starting the application."""
    import sys
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ['--capture-caret']:
        from core.client.caret_worker import main as capture_main
        return capture_main(arguments[1:])
    if arguments[:1] == ['settings']:
        from core.settings_cli import main as settings_main
        return settings_main(arguments[1:])
    from core.client.cli import parse_client_command
    command = parse_client_command(arguments)

    # Keep help, version, and argument errors independent of audio and UI imports.
    from core.client.app import CapsWriterClient

    return CapsWriterClient(command).start()

if __name__ == "__main__":
    raise SystemExit(main())
