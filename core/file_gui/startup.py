"""Place only a newly opened file window on the interactive screen."""

from PySide6.QtCore import QPoint, QRect, QSize, QTimer
from PySide6.QtWidgets import QApplication

from core.settings_gui.shell import show_window


def centered_position(frame_size: QSize, available: QRect) -> QPoint:
    """Keep the title bar reachable even when minimum size exceeds the work area."""
    return QPoint(
        available.x() + max(0, (available.width() - frame_size.width()) // 2),
        available.y() + max(0, (available.height() - frame_size.height()) // 2),
    )


def _place(window, available):
    frame_extra = window.frameGeometry().size() - window.size()
    room = (available.size() - frame_extra).expandedTo(QSize(1, 1))
    window.resize(window.size().boundedTo(room))
    window.move(centered_position(window.frameGeometry().size(), available))


def _place_on_primary(window):
    screen = QApplication.primaryScreen() or window.screen()
    if screen is not None:
        _place(window, screen.availableGeometry())


def show_initial_window(window):
    """Center once at startup; later activation preserves the user's position."""
    _place_on_primary(window)
    show_window(window)
    # Native frame margins and DPI conversion settle after the initial show.
    # Reacquire the screen in case the display layout changed in the meantime.
    QTimer.singleShot(0, window, lambda: _place_on_primary(window))
