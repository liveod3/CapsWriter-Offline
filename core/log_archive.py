"""Archive diagnostics by date/process; expire only archives owned by this handler."""

from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import RotatingFileHandler
import os
import re


class DiagnosticArchiveHandler(RotatingFileHandler):
    def __init__(self, root: Path, name: str, retention_days: int = 30):
        self.root = Path(root).resolve() / "diagnostics"
        self.name_prefix = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        self.retention_days = max(0, int(retention_days))
        self._date = datetime.now().date()
        self._session = f"{datetime.now():%H%M%S}-{os.getpid()}"
        path = self._path(datetime.now())
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8", delay=True
        )
        self._cleanup()

    def _path(self, moment: datetime) -> Path:
        return (
            self.root
            / f"{moment:%Y}"
            / f"{moment:%m}"
            / f"{self.name_prefix}_{moment:%Y%m%d}-{self._session}.log"
        )

    def _cleanup(self):
        if not self.retention_days or not self.root.exists():
            return
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        for path in self.root.glob(f"[0-9][0-9][0-9][0-9]/[0-9][0-9]/{self.name_prefix}_*.log*"):
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(self.root) or path.is_symlink():
                    continue
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink()
            except OSError:
                continue

    def emit(self, record):
        try:
            self._emit_record(record)
        except Exception:
            self.handleError(record)

    def _emit_record(self, record):
        moment = datetime.fromtimestamp(record.created)
        if moment.date() != self._date:
            if self.stream:
                self.stream.close()
                self.stream = None
            path = self._path(moment)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.baseFilename = str(path)
            self._date = moment.date()
            self._cleanup()
        super().emit(record)
