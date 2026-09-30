"""Use the platform file picker; folders enter through drag/drop or paste."""

from PySide6.QtWidgets import QFileDialog

from core.i18n import Notice, tr


def choose_paths(parent, extensions) -> list[str]:
    """Select existing media files with the standard native multi-file dialog."""
    patterns = ' '.join('*.' + value.lstrip('.') for value in sorted(extensions)) or '*'
    try:
        paths, _ = QFileDialog.getOpenFileNames(
            parent, tr('files.picker_title'), '', tr('files.filter', patterns=patterns),
        )
        return paths
    except Exception:
        raise RuntimeError(Notice('files.picker_unavailable')) from None
