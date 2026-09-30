"""Check initial placement independently of real monitor coordinates."""

import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication, QMainWindow

from core.file_gui.startup import _place, centered_position


@pytest.fixture(scope='module')
def qt_app():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('available,size,expected', [
    (QRect(0, 0, 1920, 1040), QSize(1280, 860), QPoint(320, 90)),
    (QRect(-1920, 0, 1920, 1040), QSize(1280, 860), QPoint(-1600, 90)),
    (QRect(0, -1440, 2560, 1400), QSize(1280, 860), QPoint(640, -1170)),
    (QRect(-1600, -900, 1600, 860), QSize(2000, 1000), QPoint(-1600, -900)),
])
def test_center_uses_work_area_origin_and_keeps_oversized_title_bar_reachable(available, size, expected):
    assert centered_position(size, available) == expected


class SyntheticWindow:
    def __init__(self, position, size, decoration=QSize(16, 40)):
        self.position = position
        self.dimensions = size
        self.decoration = decoration

    def frameGeometry(self):
        return QRect(self.position, self.dimensions + self.decoration)

    def size(self):
        return self.dimensions

    def resize(self, size):
        self.dimensions = size

    def move(self, point):
        self.position = point


@pytest.mark.parametrize('available', [QRect(0, 0, 1280, 680), QRect(-1600, 100, 1600, 860)])
def test_initial_placement_recovers_offscreen_frame_and_fits_native_margins(available):
    window = SyntheticWindow(QPoint(-9000, 7000), QSize(1280, 860))
    _place(window, available)
    assert available.contains(window.frameGeometry())
    assert window.frameGeometry().center() == available.center()


def test_native_margin_adjustment_runs_once_and_later_activation_keeps_position(monkeypatch, qt_app):
    from core.file_gui import startup

    available = QRect(100, 50, 1000, 700)
    monkeypatch.setattr(QApplication, 'primaryScreen', lambda: SimpleNamespace(availableGeometry=lambda: available))
    window = QMainWindow()
    window.resize(600, 400)
    window.move(-9000, 7000)
    try:
        startup.show_initial_window(window)
        qt_app.processEvents()
        assert available.contains(window.frameGeometry())
        centered = window.pos()
        chosen = centered + QPoint(37, 23)
        window.move(chosen)
        startup.show_window(window)
        qt_app.processEvents()
        assert window.pos() == chosen
    finally:
        window.close()
        window.deleteLater()
        qt_app.processEvents()


def test_deferred_adjustment_reacquires_screen_after_display_change(monkeypatch, qt_app):
    from core.file_gui import startup

    screens = [SimpleNamespace(availableGeometry=lambda: QRect(-1800, 0, 1800, 1000))]
    monkeypatch.setattr(QApplication, 'primaryScreen', lambda: screens[0])
    window = QMainWindow()
    window.resize(600, 400)
    try:
        startup.show_initial_window(window)
        available = QRect(0, 40, 1200, 760)
        screens[0] = SimpleNamespace(availableGeometry=lambda: available)
        qt_app.processEvents()
        assert available.contains(window.frameGeometry())
    finally:
        window.close()
        window.deleteLater()
        qt_app.processEvents()


def test_actual_minimum_size_keeps_title_bar_on_a_smaller_monitor(qt_app):
    window = QMainWindow()
    window.setMinimumSize(1060, 700)
    window.resize(1280, 860)
    available = QRect(-1400, 100, 800, 600)
    try:
        _place(window, available)
        assert window.size() == window.minimumSize()
        assert window.frameGeometry().topLeft() == available.topLeft()
        assert available.intersects(window.frameGeometry())
    finally:
        window.close()
        window.deleteLater()
        qt_app.processEvents()
