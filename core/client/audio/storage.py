"""Resolve recording storage independently of the current working directory."""

import os
from pathlib import Path


def recording_directory(config, base_dir: Path) -> Path:
    setting = getattr(config, "audio_dir", "")
    if not isinstance(setting, str) or "\0" in setting:
        raise ValueError("Invalid audio_dir")
    if setting.strip():
        path = Path(os.path.expandvars(setting)).expanduser()
        # Reject drive-relative paths such as D:audio on Windows.
        if path.drive and not path.is_absolute():
            raise ValueError("audio_dir must be absolute or relative to the application")
        return (base_dir / path).resolve()
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / ".local" / "share"
    return root / "CapsWriter-Offline" / "audio"
