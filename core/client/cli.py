# coding: utf-8
"""CapsWriter 客户端命令行解析。"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

from config_client import ClientConfig as Config, __version__


class ClientMode(str, Enum):
    """客户端运行模式。"""

    MIC = "mic"
    TRANSCRIBE = "transcribe"
    REBUILD_SRT = "rebuild-srt"


OUTPUT_FORMATS = frozenset({"srt", "txt", "json", "merge"})


@dataclass(frozen=True)
class ClientCommand:
    """完成校验后的单次客户端运行参数。"""

    mode: ClientMode
    inputs: tuple[Path, ...] = ()
    output_formats: frozenset[str] = frozenset()
    recursive: bool = True
    text_file: Path | None = None
    json_file: Path | None = None


def configured_output_formats() -> frozenset[str]:
    """从兼容旧配置的布尔开关生成默认输出格式。"""
    enabled = {
        name
        for name, attribute, default in (
            ("srt", "file_save_srt", True),
            ("txt", "file_save_txt", True),
            ("json", "file_save_json", True),
            ("merge", "file_save_merge", False),
        )
        if bool(getattr(Config, attribute, default))
    }
    return frozenset(enabled)


def _parse_output_formats(values: list[str] | None) -> frozenset[str]:
    if values is None:
        return configured_output_formats()

    selected = {
        item.strip().lower()
        for value in values
        for item in value.split(",")
        if item.strip()
    }
    invalid = sorted(selected - OUTPUT_FORMATS)
    if invalid:
        raise ValueError(
            f"不支持的输出格式: {', '.join(invalid)}；"
            f"可选值: {', '.join(sorted(OUTPUT_FORMATS))}"
        )
    if not selected:
        raise ValueError("--format 至少需要指定一种输出格式")
    return frozenset(selected)


def _absolute_path(value: str | Path, cwd: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(strict=False)


def _require_existing_path(parser: argparse.ArgumentParser, path: Path) -> None:
    if not path.exists():
        parser.error(f"输入路径不存在: {path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="start_client",
        description="CapsWriter Offline 客户端",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("mic", help="启动麦克风实时语音输入")

    transcribe = subparsers.add_parser(
        "transcribe",
        help="转写一个或多个媒体文件、文件夹",
    )
    transcribe.add_argument(
        "inputs",
        nargs="+",
        metavar="INPUT",
        help="媒体文件或文件夹路径",
    )
    transcribe.add_argument(
        "-f",
        "--format",
        action="append",
        dest="formats",
        metavar="FORMAT",
        help="输出格式，可重复或用逗号分隔: srt,txt,json,merge",
    )
    recursive = transcribe.add_mutually_exclusive_group()
    recursive.add_argument(
        "--recursive",
        action="store_true",
        dest="recursive",
        help="递归扫描输入文件夹",
    )
    recursive.add_argument(
        "--no-recursive",
        action="store_false",
        dest="recursive",
        help="只扫描输入文件夹第一层",
    )
    transcribe.set_defaults(recursive=None)

    rebuild = subparsers.add_parser(
        "rebuild-srt",
        help="使用指定 TXT 和时间戳 JSON 重建 SRT",
    )
    rebuild.add_argument(
        "-t",
        "--text",
        required=True,
        metavar="TXT",
        help="人工校对后的 TXT 文件",
    )
    rebuild.add_argument(
        "-j",
        "--json",
        required=True,
        metavar="JSON",
        help="包含 tokens 和 timestamps 的 JSON 文件",
    )
    return parser


def _normalize_compatibility_args(
    parser: argparse.ArgumentParser,
    arguments: list[str],
    cwd: Path,
) -> list[str]:
    """将双击、拖拽和旧式裸路径调用转换成正式子命令。"""
    if not arguments:
        return [ClientMode.MIC.value]

    if arguments[0] in {mode.value for mode in ClientMode}:
        return arguments
    if arguments[0] in {"-h", "--help", "--version"}:
        return arguments
    if any(value.startswith("-") for value in arguments):
        return arguments

    paths = [_absolute_path(value, cwd) for value in arguments]
    if not all(path.exists() for path in paths):
        return arguments

    text_files = [path for path in paths if path.suffix.lower() == ".txt"]
    json_files = [path for path in paths if path.suffix.lower() == ".json"]
    subtitle_inputs = [
        path
        for path in paths
        if path.suffix.lower() in {".txt", ".json", ".srt", ".vtt"}
    ]

    if subtitle_inputs:
        if (
            len(paths) == 2
            and len(text_files) == 1
            and len(json_files) == 1
        ):
            return [
                ClientMode.REBUILD_SRT.value,
                "--text",
                str(text_files[0]),
                "--json",
                str(json_files[0]),
            ]
        parser.error(
            "字幕重建必须同时且仅传入一个 TXT 和一个 JSON；"
            "媒体文件请与字幕重建任务分开处理"
        )

    return [ClientMode.TRANSCRIBE.value, *map(str, paths)]


def parse_client_command(
    argv: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
) -> ClientCommand:
    """解析正式 CLI，并兼容无参数、拖拽和旧式裸路径调用。"""
    parser = _build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    startup_cwd = (cwd or Path.cwd()).resolve(strict=False)
    arguments = _normalize_compatibility_args(parser, arguments, startup_cwd)
    namespace = parser.parse_args(arguments)
    mode = ClientMode(namespace.command)

    if mode is ClientMode.MIC:
        return ClientCommand(mode=mode)

    if mode is ClientMode.TRANSCRIBE:
        inputs = tuple(
            _absolute_path(value, startup_cwd) for value in namespace.inputs
        )
        for path in inputs:
            _require_existing_path(parser, path)
        try:
            output_formats = _parse_output_formats(namespace.formats)
        except ValueError as exc:
            parser.error(str(exc))
        if not output_formats:
            parser.error(
                "没有启用任何输出格式；请使用 --format，或修改 config_client.py"
            )
        configured_recursive = bool(
            getattr(Config, "file_scan_recursive", True)
        )
        recursive = (
            configured_recursive
            if namespace.recursive is None
            else namespace.recursive
        )
        return ClientCommand(
            mode=mode,
            inputs=inputs,
            output_formats=output_formats,
            recursive=recursive,
        )

    text_file = _absolute_path(namespace.text, startup_cwd)
    json_file = _absolute_path(namespace.json, startup_cwd)
    _require_existing_path(parser, text_file)
    _require_existing_path(parser, json_file)
    if text_file.suffix.lower() != ".txt":
        parser.error(f"--text 必须指向 .txt 文件: {text_file}")
    if json_file.suffix.lower() != ".json":
        parser.error(f"--json 必须指向 .json 文件: {json_file}")
    return ClientCommand(
        mode=mode,
        text_file=text_file,
        json_file=json_file,
    )


__all__ = [
    "ClientCommand",
    "ClientMode",
    "OUTPUT_FORMATS",
    "configured_output_formats",
    "parse_client_command",
]
