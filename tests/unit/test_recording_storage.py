"""Recording paths, legacy preservation and unavailable destinations."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from config_client import ClientConfig
from core.client.audio.storage import recording_directory
from core.client.audio.file_manager import AudioFileManager
from core.client.diary.diary_writer import DiaryWriter


def test_default_portable_absolute_and_legacy_default(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user data"))
    base = tmp_path / "app"
    default = tmp_path / "user data" / "CapsWriter-Offline" / "audio"
    assert recording_directory(SimpleNamespace(), base) == default
    assert recording_directory(SimpleNamespace(audio_dir=""), base) == default
    assert recording_directory(SimpleNamespace(audio_dir="audio-data"), base) == base / "audio-data"
    assert (
        recording_directory(SimpleNamespace(audio_dir=str(tmp_path / "custom")), base)
        == tmp_path / "custom"
    )


def test_new_recording_and_link_preserve_legacy_data(tmp_path, monkeypatch):
    monkeypatch.setattr(ClientConfig, "audio_dir", "audio-data", raising=False)
    monkeypatch.setattr("core.client.audio.file_manager.shutil.which", lambda _: None)
    legacy = tmp_path / "2026" / "09" / "assets" / "legacy.wav"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy fixture")
    manager = AudioFileManager(base_dir=tmp_path)
    path, _ = manager.create(1, 0)
    manager.write(np.zeros((480, 1), dtype=np.float32))
    manager.finish()
    assert path.is_relative_to(tmp_path / "audio-data")
    diary = DiaryWriter(tmp_path / "transcripts")
    entry = diary.write("synthetic", 0, path)
    assert "audio-data/" in entry.read_text(encoding="utf-8")
    assert legacy.read_bytes() == b"legacy fixture"


def test_cross_drive_diary_uses_uri(tmp_path, monkeypatch):
    monkeypatch.setattr("os.path.relpath", Mock(side_effect=ValueError("different drive")))
    path = tmp_path / "audio with spaces.wav"
    entry = DiaryWriter(tmp_path / "transcripts").write("synthetic", 0, path)
    assert f"[Audio](<{path.as_uri()}>)" in entry.read_text(encoding="utf-8")


def test_unwritable_destination_has_no_fallback(tmp_path, monkeypatch):
    blocked = tmp_path / "file-instead-of-directory"
    blocked.write_text("untouched")
    monkeypatch.setattr(ClientConfig, "audio_dir", str(blocked), raising=False)
    manager = AudioFileManager(base_dir=tmp_path)
    with pytest.raises(OSError):
        manager.create(1, 0)
    assert manager.file_handle is None
    assert blocked.read_text() == "untouched"


def test_storage_permission_failure_keeps_dictation_available(monkeypatch):
    from core.client.audio.recorder import AudioRecorder

    monkeypatch.setattr("core.ui.show_status_hint", Mock())

    async def run():
        app = SimpleNamespace(state=SimpleNamespace(register_audio_file=Mock()))
        recorder = AudioRecorder(app)
        recorder._file_manager = SimpleNamespace(create=Mock())
        writer = SimpleNamespace(call=AsyncMock(side_effect=PermissionError()), close=AsyncMock())
        recorder._writer = writer
        assert await recorder._create_recording_file(1) is None
        assert recorder._writer is None
        writer.close.assert_awaited_once_with(abort=True)
        app.state.register_audio_file.assert_not_called()

    asyncio.run(run())
