# coding: utf-8
from __future__ import annotations

from typing import Sequence

from core.client.cli import parse_client_command


def main(argv: Sequence[str] | None = None) -> int:
    """解析客户端命令后再加载和启动实际应用。"""
    command = parse_client_command(argv)

    # 保持帮助、版本和参数错误路径轻量，不提前加载音频与 UI 模块。
    from core.client.app import CapsWriterClient

    return CapsWriterClient(command).start()

if __name__ == "__main__":
    raise SystemExit(main())
