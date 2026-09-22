"""Resolve recording storage independently of the current working directory."""

from core.i18n import Notice

import os
from pathlib import Path


def recording_directory(config, base_dir: Path) -> Path:
    setting = getattr(config, "audio_dir", "")
    if not isinstance(setting, str) or "\0" in setting:
        raise ValueError(Notice('validation.storage.invalid_audio_dir'))
    if setting.strip():
        path = Path(os.path.expandvars(setting)).expanduser()
        # Reject drive-relative paths such as D:audio on Windows.
        if path.drive and not path.is_absolute():
            raise ValueError(Notice('validation.storage.audio_dir_must_be_absolute_or_relative_to'))
        return (base_dir / path).resolve()
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / ".local" / "share"
    return root / "CapsWriter-Offline" / "audio"
