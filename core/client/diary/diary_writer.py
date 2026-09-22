"""独立的文字与文本动作归档，不依赖音频保存开关。"""

from __future__ import annotations
from datetime import datetime
from pathlib import Path
from threading import Lock


class DiaryWriter:
    def __init__(self, base_path: Path | None = None):
        self.base_path = Path(base_path or ".")
        self._lock = Lock()

    def write(
        self,
        text: str,
        time_start: float,
        file_audio: Path | None = None,
        *,
        original: str | None = None,
        action_input: str | None = None,
    ) -> Path:
        moment = datetime.fromtimestamp(time_start)
        folder = self.base_path / moment.strftime("%Y") / moment.strftime("%m")
        entry = f"### {moment:%H:%M:%S}\n\n{text}\n\n"
        if original is not None and original != text:
            entry += f"Original transcription:\n\n{original}\n\n"
        if action_input is not None:
            entry += f"Action input:\n\n{action_input}\n\n"
        if file_audio is not None:
            import os

            try:
                relative = os.path.relpath(file_audio.resolve(), folder.resolve()).replace("\\", "/")
            except ValueError:
                # Windows relpath cannot cross volumes; file URIs remain clickable.
                relative = file_audio.resolve().as_uri()
            entry += f"[Audio](<{relative}>)\n\n"
        with self._lock:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{moment:%d}.md"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(entry)
        return path
