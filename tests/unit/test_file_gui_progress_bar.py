"""Confirmed values render directly, including zero and unknown-duration states."""

import hashlib
import os

import pytest

from core.file_gui.progress_bar import FileProgressBar


@pytest.fixture(scope='module')
def qt_app():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def filled_pixels(bar):
    image = bar.grab().toImage()
    row = image.height() // 2
    pixels = [image.pixelColor(x, row) for x in range(image.width())]
    assert all(color in (bar.FILL_COLOR, bar.TRACK_COLOR) for color in pixels)
    return pixels.count(bar.FILL_COLOR), image.width()


@pytest.mark.parametrize('minimum,maximum,value,fraction', [
    (0, 1000, 0, 0),
    (0, 1000, 250, 0.25),
    (0, 1000, 1000, 1),
    (20, 40, 30, 0.5),
    (0, 0, 0, 0),
])
def test_fill_is_exact_solid_fraction_including_empty_unknown_range(qt_app, minimum, maximum, value, fraction):
    bar = FileProgressBar()
    bar.resize(200, 8)
    bar.setRange(minimum, maximum)
    bar.setValue(value)
    filled, width = filled_pixels(bar)
    assert filled == int(width * fraction)
    bar.close()


def test_value_changes_update_immediately_without_interpolation(qt_app):
    bar = FileProgressBar()
    bar.resize(200, 8)
    bar.setRange(0, 1000)
    previous = 0
    for value in (0, 1, 5, 10, 100, 250, 500, 999, 1000):
        bar.setValue(value)
        filled, width = filled_pixels(bar)
        assert filled == int(width * value / 1000)
        assert filled >= previous
        previous = filled
    bar.reset()
    assert filled_pixels(bar)[0] == 0
    bar.close()


def test_native_accessibility_retains_progress_role_and_actual_value(qt_app):
    from PySide6.QtGui import QAccessible
    bar = FileProgressBar()
    bar.setRange(0, 1000)
    bar.setValue(370)
    interface = QAccessible.queryAccessibleInterface(bar)
    assert interface.role() == QAccessible.Role.ProgressBar
    values = interface.valueInterface()
    assert values.minimumValue() == 0
    assert values.maximumValue() == 1000
    assert values.currentValue() == 370
    bar.close()


def test_fixed_value_has_identical_pixels_across_timer_ticks_and_native_qss(qt_app):
    from PySide6.QtTest import QTest
    bar = FileProgressBar()
    bar.resize(240, 8)
    bar.setStyleSheet('QProgressBar {border: none; border-radius: 3px;} '
                     'QProgressBar::chunk {background: red; border-radius: 3px;}')
    bar.setRange(0, 1000)
    bar.setValue(370)
    bar.show()
    hashes = set()
    for _ in range(8):
        QTest.qWait(25)
        picture = bar.grab().toImage()
        hashes.add(hashlib.sha256(bytes(picture.constBits())).digest())
    assert len(hashes) == 1
    assert filled_pixels(bar)[0] > 0
    bar.close()


def test_chunk_cells_change_only_at_confirmed_boundaries(qt_app):
    bar = FileProgressBar()
    bar.resize(200, 18)
    bar.set_chunks(2, 5, True)
    image = bar.grab().toImage()
    assert image.pixelColor(20, 9) == bar.FILL_COLOR
    assert image.pixelColor(60, 9) == bar.FILL_COLOR
    assert image.pixelColor(100, 9) == bar.ACTIVE_COLOR
    assert image.pixelColor(140, 9) == bar.TRACK_COLOR
    assert image.pixelColor(180, 9) == bar.TRACK_COLOR
    before = bytes(image.constBits())
    bar.setValue(950)
    assert bytes(bar.grab().toImage().constBits()) == before
    bar.set_chunks(5, 5, False)
    image = bar.grab().toImage()
    assert all(image.pixelColor(x, 9) == bar.FILL_COLOR for x in (20, 60, 100, 140, 180))
    bar.close()


def test_large_or_unknown_chunk_count_uses_a_bounded_visible_window(qt_app):
    bar = FileProgressBar()
    bar.resize(400, 18)
    for total in (1000000, None):
        bar.set_chunks(900000, total, True)
        start, end = bar.visible_chunks()
        assert 0 <= start <= 900000 < end
        assert end - start <= 25
    bar.close()


@pytest.mark.parametrize('seconds,total,ratio', [(150, 3, 0.5), (165, 3, 0.75), (125, 2, 65 / 60)])
def test_tail_width_is_proportional_to_audio_including_long_overlap_tail(qt_app, seconds, total, ratio):
    bar = FileProgressBar()
    bar.resize(400, 18)
    bar.set_chunks(total - 1, total, True, segment_seconds=60, audio_seconds=seconds)
    blocks = bar.chunk_rects()
    first, last = blocks[0][1], blocks[-1][1]
    assert last.width() / first.width() == pytest.approx(ratio)
    assert last.right() == pytest.approx(bar.contentsRect().right())
    picture = bar.grab().toImage()
    assert picture.pixelColor(int(first.center().x()), 9) == bar.FILL_COLOR
    assert picture.pixelColor(int(last.center().x()), 9) == bar.ACTIVE_COLOR
    bar.close()


def test_long_file_viewport_and_unknown_duration_preserve_proportions(qt_app):
    bar = FileProgressBar()
    bar.resize(400, 18)
    bar.set_chunks(999, 1000, True, segment_seconds=60, audio_seconds=59970)
    blocks = bar.chunk_rects()
    assert len(blocks) <= 25 and blocks[-1][0] == 999
    assert blocks[-1][1].width() / blocks[0][1].width() == pytest.approx(0.5)
    bar.set_chunks(2, None, True, segment_seconds=60)
    blocks = bar.chunk_rects()
    assert blocks[0][1].width() == blocks[-1][1].width()
    bar.set_chunks(3, 3, False, segment_seconds=60, audio_seconds=150)
    blocks = bar.chunk_rects()
    assert blocks[-1][1].width() / blocks[0][1].width() == pytest.approx(0.5)
    bar.close()
