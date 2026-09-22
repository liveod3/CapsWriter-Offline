import json
from pathlib import Path

import pytest

from core.client.transcribe.srt_adjuster import SrtAdjuster


def test_load_words_validates_and_normalizes_timestamp_json(
    tmp_path: Path,
) -> None:
    json_file = tmp_path / "timestamps.json"
    json_file.write_text(
        json.dumps(
            {"tokens": ["你", "@好"], "timestamps": [0, 0.3]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    words = SrtAdjuster._load_words(json_file)

    assert words == [
        {"word": "你", "start": 0.0, "end": 0.2},
        {"word": "好", "start": 0.3, "end": 0.5},
    ]


def test_load_words_rejects_decreasing_timestamps(tmp_path: Path) -> None:
    json_file = tmp_path / "timestamps.json"
    json_file.write_text(
        json.dumps({"tokens": ["a", "b"], "timestamps": [1.0, 0.5]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="nondecreasing"):
        SrtAdjuster._load_words(json_file)


def test_allocate_output_does_not_overwrite_existing_srt(
    tmp_path: Path,
) -> None:
    text_file = tmp_path / "edited.txt"
    text_file.touch()
    text_file.with_suffix(".srt").touch()

    output, sequence = SrtAdjuster._allocate_output(text_file)

    assert output == tmp_path / "edited (2).srt"
    assert sequence == 2


def test_adjust_rebuilds_srt_from_explicit_text_and_json(
    tmp_path: Path,
) -> None:
    text_file = tmp_path / "edited.txt"
    json_file = tmp_path / "source-timestamps.json"
    text_file.write_text("你好\n", encoding="utf-8")
    json_file.write_text(
        json.dumps(
            {"tokens": ["你", "好"], "timestamps": [0.0, 0.3]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert SrtAdjuster().adjust(text_file, json_file) is True
    assert text_file.with_suffix(".srt").exists()

    assert SrtAdjuster().adjust(text_file, json_file) is True
    assert (tmp_path / "edited (2).srt").exists()
