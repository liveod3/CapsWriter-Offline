from pathlib import Path

from core.client.transcribe.result_handler import ResultHandler


def test_output_paths_only_include_task_formats(tmp_path: Path) -> None:
    source = tmp_path / "lecture.mp4"

    paths = ResultHandler.output_paths(source, frozenset({"srt", "merge"}))

    assert paths == {
        "merge": tmp_path / "lecture.merge.txt",
        "srt": tmp_path / "lecture.srt",
    }


def test_allocate_output_file_uses_one_sequence_for_selected_formats(
    tmp_path: Path,
) -> None:
    source = tmp_path / "lecture.mp4"
    (tmp_path / "lecture.txt").touch()

    candidate, sequence, paths = ResultHandler.allocate_output_file(
        source,
        frozenset({"txt", "json"}),
    )

    assert candidate == tmp_path / "lecture (2).mp4"
    assert sequence == 2
    assert paths == {
        "txt": tmp_path / "lecture (2).txt",
        "json": tmp_path / "lecture (2).json",
    }
