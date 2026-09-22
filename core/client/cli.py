# coding: utf-8
"""CapsWriter 客户端命令行解析。"""

from __future__ import annotations

from core.i18n import tr, set_language
from core.i18n.argparse import localize_parser_error

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


class LocalizedHelpFormatter(argparse.HelpFormatter):
    def start_section(self, heading):
        heading_id = {'options': 'cli.options', 'positional arguments': 'cli.positionals'}.get(heading)
        super().start_section(tr(heading_id) if heading_id else heading)

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, tr('cli.usage') if prefix is None else prefix)


class LocalizedArgumentParser(argparse.ArgumentParser):
    """Localize product help without changing argparse's process-global gettext."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('formatter_class', LocalizedHelpFormatter)
        kwargs['add_help'] = False
        super().__init__(*args, **kwargs)
        self.add_argument('-h', '--help', action='help', help=tr('cli.help'))

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, tr('cli.error', prog=self.prog, message=localize_parser_error(message)))


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
            tr('cli.invalid_formats', value0=', '.join(invalid), value1=', '.join(sorted(OUTPUT_FORMATS)))
        )
    if not selected:
        raise ValueError(tr('cli.empty_formats'))
    return frozenset(selected)


def _absolute_path(value: str | Path, cwd: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(strict=False)


def _require_existing_path(parser: argparse.ArgumentParser, path: Path) -> None:
    if not path.exists():
        parser.error(tr('cli.missing_path', value0=path))


def _build_parser() -> argparse.ArgumentParser:
    parser = LocalizedArgumentParser(
        prog="start_client",
        description=tr('cli.description'),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help=tr('cli.version_help'),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("mic", help=tr('cli.mic'))

    transcribe = subparsers.add_parser(
        "transcribe",
        help=tr('cli.transcribe'),
    )
    transcribe.add_argument(
        "inputs",
        nargs="+",
        metavar="INPUT",
        help=tr('cli.inputs'),
    )
    transcribe.add_argument(
        "-f",
        "--format",
        action="append",
        dest="formats",
        metavar="FORMAT",
        help=tr('cli.formats'),
    )
    recursive = transcribe.add_mutually_exclusive_group()
    recursive.add_argument(
        "--recursive",
        action="store_true",
        dest="recursive",
        help=tr('cli.recursive'),
    )
    recursive.add_argument(
        "--no-recursive",
        action="store_false",
        dest="recursive",
        help=tr('cli.no_recursive'),
    )
    transcribe.set_defaults(recursive=None)

    rebuild = subparsers.add_parser(
        "rebuild-srt",
        help=tr('cli.rebuild'),
    )
    rebuild.add_argument(
        "-t",
        "--text",
        required=True,
        metavar="TXT",
        help=tr('cli.text'),
    )
    rebuild.add_argument(
        "-j",
        "--json",
        required=True,
        metavar="JSON",
        help=tr('cli.json'),
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
            tr('cli.rebuild_inputs')
        )

    return [ClientMode.TRANSCRIBE.value, *map(str, paths)]


def parse_client_command(
    argv: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
) -> ClientCommand:
    """解析正式 CLI，并兼容无参数、拖拽和旧式裸路径调用。"""
    set_language(getattr(Config, "ui_language", "auto"))
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
                tr('cli.no_formats')
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
        parser.error(tr('cli.invalid_text', value0=text_file))
    if json_file.suffix.lower() != ".json":
        parser.error(tr('cli.invalid_json', value0=json_file))
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
