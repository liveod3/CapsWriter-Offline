from pathlib import Path
from unittest.mock import patch

import pytest

from config_client import ClientConfig
from core.client.cli import ClientMode, parse_client_command


def test_no_arguments_selects_microphone_mode(tmp_path: Path) -> None:
    command = parse_client_command([], cwd=tmp_path)

    assert command.mode is ClientMode.MIC


def test_transcribe_parses_formats_and_recursive_override(tmp_path: Path) -> None:
    media = tmp_path / "lecture.mp4"
    media.touch()

    command = parse_client_command(
        [
            "transcribe",
            "--format",
            "SRT,txt",
            "-f",
            "json",
            "--no-recursive",
            str(media),
        ],
        cwd=tmp_path,
    )

    assert command.mode is ClientMode.TRANSCRIBE
    assert command.inputs == (media.resolve(),)
    assert command.output_formats == frozenset({"srt", "txt", "json"})
    assert command.recursive is False


def test_transcribe_uses_configuration_when_format_is_omitted(
    tmp_path: Path,
) -> None:
    media = tmp_path / "lecture.mp4"
    media.touch()

    with (
        patch.object(ClientConfig, "file_save_srt", False),
        patch.object(ClientConfig, "file_save_txt", True),
        patch.object(ClientConfig, "file_save_json", False),
        patch.object(ClientConfig, "file_save_merge", True),
        patch.object(ClientConfig, "file_scan_recursive", False),
    ):
        command = parse_client_command(
            ["transcribe", str(media)],
            cwd=tmp_path,
        )

    assert command.output_formats == frozenset({"txt", "merge"})
    assert command.recursive is False


def test_bare_media_paths_select_transcribe_for_drag_compatibility(
    tmp_path: Path,
) -> None:
    first = tmp_path / "a.mp3"
    second = tmp_path / "b.mp4"
    first.touch()
    second.touch()

    command = parse_client_command(
        [str(first), str(second)],
        cwd=tmp_path,
    )

    assert command.mode is ClientMode.TRANSCRIBE
    assert command.inputs == (first.resolve(), second.resolve())


def test_bare_text_and_json_select_rebuild_regardless_of_order(
    tmp_path: Path,
) -> None:
    text_file = tmp_path / "edited.txt"
    json_file = tmp_path / "original.json"
    text_file.touch()
    json_file.touch()

    command = parse_client_command(
        [str(json_file), str(text_file)],
        cwd=tmp_path,
    )

    assert command.mode is ClientMode.REBUILD_SRT
    assert command.text_file == text_file.resolve()
    assert command.json_file == json_file.resolve()


def test_bare_text_without_json_is_rejected(tmp_path: Path) -> None:
    text_file = tmp_path / "edited.txt"
    text_file.touch()

    with pytest.raises(SystemExit) as exc_info:
        parse_client_command([str(text_file)], cwd=tmp_path)

    assert exc_info.value.code == 2


def test_rebuild_accepts_differently_named_explicit_inputs(
    tmp_path: Path,
) -> None:
    text_file = tmp_path / "edited.txt"
    json_file = tmp_path / "timestamps-from-source.json"
    text_file.touch()
    json_file.touch()

    command = parse_client_command(
        [
            "rebuild-srt",
            "--text",
            str(text_file),
            "--json",
            str(json_file),
        ],
        cwd=tmp_path,
    )

    assert command.text_file == text_file.resolve()
    assert command.json_file == json_file.resolve()


def test_invalid_output_format_is_rejected(tmp_path: Path) -> None:
    media = tmp_path / "lecture.mp4"
    media.touch()

    with pytest.raises(SystemExit) as exc_info:
        parse_client_command(
            ["transcribe", "--format", "docx", str(media)],
            cwd=tmp_path,
        )

    assert exc_info.value.code == 2
