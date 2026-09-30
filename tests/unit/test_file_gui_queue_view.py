"""Drive wheel animations by logical time rather than timing desktop frames."""

import os

import pytest
from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QAccessible, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView, QAbstractSlider, QListWidget, QListWidgetItem

from core.file_gui.queue_view import FileQueueList


@pytest.fixture(scope='module')
def qt_app():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    return QApplication.instance() or QApplication([])


def populate(widget):
    widget.resize(340, 300)
    widget.setSpacing(2)
    for index in range(30):
        item = QListWidgetItem(f'Synthetic file {index}.wav')
        item.setSizeHint(QSize(220, 70))
        widget.addItem(item)
    widget.show()
    return widget


@pytest.fixture
def queue(qt_app):
    widget = populate(FileQueueList())
    qt_app.processEvents()
    yield widget
    widget.close()
    qt_app.processEvents()


def wheel(queue, *, angle=-120, pixels=0, over_scrollbar=False):
    target = queue.verticalScrollBar() if over_scrollbar else queue.viewport()
    position = target.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(target.mapToGlobal(position)),
                        QPoint(0, pixels), QPoint(0, angle), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(target, event)
    assert event.isAccepted()


def finish(queue):
    queue._wheel_animation.setCurrentTime(queue._wheel_animation.duration())


def test_each_notch_eases_one_actual_row_and_never_jumps_a_page(queue):
    assert queue.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    row_height = queue.visualItemRect(queue.item(1)).top() - queue.visualItemRect(queue.item(0)).top()
    wheel(queue)
    assert queue.verticalScrollBar().value() == 0
    assert queue._wheel_animation.endValue() == row_height
    queue._wheel_animation.setCurrentTime(50)
    halfway = queue.verticalScrollBar().value()
    assert 0 < halfway < row_height < queue.verticalScrollBar().pageStep()
    finish(queue)
    assert queue.verticalScrollBar().value() == row_height
    assert queue._wheel_animation.state() == QAbstractAnimation.State.Stopped


def test_repeated_and_fractional_notches_accumulate_without_losing_target(queue):
    step = queue._row_step()
    wheel(queue)
    wheel(queue)
    wheel(queue)
    assert queue._wheel_animation.endValue() == 3 * step
    finish(queue)
    wheel(queue, angle=-60)
    wheel(queue, angle=-60)
    assert queue._wheel_animation.endValue() == 4 * step
    finish(queue)
    assert queue.verticalScrollBar().value() == 4 * step


def test_direction_change_reverses_from_visible_position_without_finishing_queued_travel(queue):
    bar = queue.verticalScrollBar()
    bar.setValue(300)
    wheel(queue)
    wheel(queue)
    queue._wheel_animation.setCurrentTime(50)
    visible = bar.value()
    wheel(queue, angle=120)
    assert queue._wheel_animation.startValue() == visible
    assert queue._wheel_animation.endValue() == visible - queue._row_step()
    queue._wheel_animation.setCurrentTime(50)
    assert bar.value() < visible
    finish(queue)
    assert bar.value() == visible - queue._row_step()


def test_wheel_targets_clamp_at_both_bounds_and_scrollbar_wheel_uses_same_row_step(queue):
    bar = queue.verticalScrollBar()
    bar.setValue(bar.maximum() - 10)
    wheel(queue, over_scrollbar=True)
    assert queue._wheel_animation.endValue() == bar.maximum()
    finish(queue)
    assert bar.value() == bar.maximum()
    wheel(queue, angle=12000)
    finish(queue)
    assert bar.value() == 0
    wheel(queue, over_scrollbar=True)
    assert queue._wheel_animation.endValue() == queue._row_step()


def test_touchpad_pixels_remain_exact_and_cancel_pending_wheel_animation(queue):
    bar = queue.verticalScrollBar()
    wheel(queue, pixels=-17)
    assert bar.value() == 17
    assert queue._wheel_animation.state() == QAbstractAnimation.State.Stopped
    wheel(queue)
    queue._wheel_animation.setCurrentTime(50)
    visible = bar.value()
    wheel(queue, pixels=-9)
    assert bar.value() == visible + 9
    assert queue._wheel_animation.state() == QAbstractAnimation.State.Stopped
    wheel(queue, pixels=3)
    assert bar.value() == visible + 6


@pytest.mark.parametrize('key', [Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown])
def test_keyboard_scrolling_cancels_animation_and_matches_native_list(queue, qt_app, key):
    native = populate(QListWidget())
    native.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    try:
        qt_app.processEvents()
        queue.setCurrentRow(10)
        wheel(queue)
        queue._wheel_animation.setCurrentTime(50)
        native.setCurrentRow(queue.currentRow())
        native.verticalScrollBar().setValue(queue.verticalScrollBar().value())
        QTest.keyClick(queue, key)
        QTest.keyClick(native, key)
        assert queue._wheel_animation.state() == QAbstractAnimation.State.Stopped
        assert queue.currentRow() == native.currentRow()
        assert queue.verticalScrollBar().value() == native.verticalScrollBar().value()
    finally:
        native.close()


def test_scrollbar_page_action_and_manual_drag_value_cancel_wheel_animation(queue):
    bar = queue.verticalScrollBar()
    wheel(queue)
    queue._wheel_animation.setCurrentTime(50)
    visible = bar.value()
    bar.triggerAction(QAbstractSlider.SliderAction.SliderPageStepAdd)
    assert queue._wheel_animation.state() == QAbstractAnimation.State.Stopped
    assert bar.value() == visible + bar.pageStep()
    wheel(queue)
    bar.sliderPressed.emit()
    bar.setSliderPosition(125)
    assert queue._wheel_animation.state() == QAbstractAnimation.State.Stopped
    assert bar.value() == 125


def test_selection_and_native_accessibility_are_preserved(queue):
    wheel(queue)
    QTest.mouseClick(queue.viewport(), Qt.MouseButton.LeftButton,
                     pos=queue.visualItemRect(queue.item(2)).center())
    assert queue.currentRow() == 2
    assert queue._wheel_animation.state() == QAbstractAnimation.State.Stopped
    assert QAccessible.queryAccessibleInterface(queue).role() == QAccessible.Role.List


@pytest.mark.parametrize('action', ['hide', 'deactivate', 'clear'])
def test_hidden_inactive_or_rebuilt_queue_stops_animation(queue, action):
    wheel(queue)
    assert queue._wheel_animation.state() == QAbstractAnimation.State.Running
    if action == 'hide':
        queue.hide()
    elif action == 'deactivate':
        QApplication.sendEvent(queue, QEvent(QEvent.Type.WindowDeactivate))
    else:
        queue.clear()
        QApplication.processEvents()
    assert queue._wheel_animation.state() == QAbstractAnimation.State.Stopped


def test_row_step_adapts_to_actual_row_height(queue, qt_app):
    queue.setSpacing(0)
    queue.item(1).setSizeHint(QSize(220, 110))
    qt_app.processEvents()
    wheel(queue)
    assert queue._wheel_animation.endValue() == 70
    finish(queue)
    wheel(queue)
    assert queue._wheel_animation.endValue() == 180


def test_destroying_queue_disposes_running_animation(qt_app):
    from PySide6.QtCore import QCoreApplication
    from shiboken6 import isValid

    widget = populate(FileQueueList())
    qt_app.processEvents()
    wheel(widget)
    animation = widget._wheel_animation
    assert animation.state() == QAbstractAnimation.State.Running
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(widget) and not isValid(animation)
    qt_app.processEvents()


def sweep_move(queue, position):
    from PySide6.QtGui import QMouseEvent
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(position), QPointF(queue.viewport().mapToGlobal(position)),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(queue.viewport(), event)


def test_checkbox_sweep_selects_skipped_rows_and_retrace_restores_original(queue):
    queue.set_check_mode(True)
    queue.clearSelection()
    start = queue.check_rect(queue.item(0)).center()
    end = queue.check_rect(queue.item(3)).center()
    QTest.mousePress(queue.viewport(), Qt.MouseButton.LeftButton, pos=start)
    sweep_move(queue, end)
    assert [queue.row(item) for item in queue.selectedItems()] == [0, 1, 2, 3]
    sweep_move(queue, queue.check_rect(queue.item(1)).center())
    assert [queue.row(item) for item in queue.selectedItems()] == [0, 1]
    sweep_move(queue, end)
    assert [queue.row(item) for item in queue.selectedItems()] == [0, 1, 2, 3]
    QTest.mouseRelease(queue.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert not queue._sweep_timer.isActive()
    # The initial checked box establishes a deselection gesture.
    QTest.mousePress(queue.viewport(), Qt.MouseButton.LeftButton, pos=start)
    sweep_move(queue, end)
    QTest.mouseRelease(queue.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert not queue.selectedItems()


def test_selection_mode_clicks_toggle_without_modifiers_and_space_is_accessible(queue):
    queue.set_check_mode(True)
    queue.clearSelection()
    for index in (0, 2, 0):
        QTest.mouseClick(queue.viewport(), Qt.MouseButton.LeftButton,
                         pos=queue.visualItemRect(queue.item(index)).center())
    assert [queue.row(item) for item in queue.selectedItems()] == [2]
    QTest.keyClick(queue, Qt.Key.Key_Space)
    assert sorted(queue.row(item) for item in queue.selectedItems()) == [0, 2]
    queue.set_check_mode(False)
    assert queue.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert sorted(queue.row(item) for item in queue.selectedItems()) == [0, 2]


@pytest.mark.parametrize('stop', ['release', 'hide', 'deactivate', 'clear', 'mode'])
def test_edge_sweep_scrolls_and_stops_on_every_exit(queue, stop):
    queue.set_check_mode(True)
    queue.clearSelection()
    QTest.mousePress(queue.viewport(), Qt.MouseButton.LeftButton, pos=queue.check_rect(queue.item(0)).center())
    sweep_move(queue, QPoint(16, queue.viewport().height() - 1))
    for _ in range(15):
        queue._sweep_scroll()
    assert queue.verticalScrollBar().value() > 0
    assert max(queue.row(item) for item in queue.selectedItems()) >= 4
    if stop == 'release':
        QTest.mouseRelease(queue.viewport(), Qt.MouseButton.LeftButton)
    elif stop == 'hide':
        queue.hide()
    elif stop == 'deactivate':
        QApplication.sendEvent(queue, QEvent(QEvent.Type.WindowDeactivate))
    elif stop == 'clear':
        queue.clear()
    else:
        queue.set_check_mode(False)
    assert not queue._sweep_timer.isActive() and queue._sweep_row is None


def test_fast_sweep_into_blank_space_reaches_last_row(queue, qt_app):
    queue.clear()
    for _ in range(2):
        item = QListWidgetItem('Synthetic short queue')
        item.setSizeHint(QSize(220, 70))
        queue.addItem(item)
    qt_app.processEvents()
    queue.set_check_mode(True)
    queue.clearSelection()
    QTest.mousePress(queue.viewport(), Qt.MouseButton.LeftButton, pos=queue.check_rect(queue.item(0)).center())
    sweep_move(queue, QPoint(16, queue.viewport().height() - 40))
    QTest.mouseRelease(queue.viewport(), Qt.MouseButton.LeftButton)
    assert len(queue.selectedItems()) == 2


def test_row_origin_sweep_crosses_anchor_and_restores_preexisting_selection(queue):
    queue.set_check_mode(True)
    queue.clearSelection()
    queue.item(0).setSelected(True)
    queue.item(2).setSelected(True)
    def point(row):
        return queue.visualItemRect(queue.item(row)).center()
    QTest.mousePress(queue.viewport(), Qt.MouseButton.LeftButton, pos=point(1))
    sweep_move(queue, point(3))
    assert sorted(queue.row(item) for item in queue.selectedItems()) == [0, 1, 2, 3]
    sweep_move(queue, point(1))
    assert sorted(queue.row(item) for item in queue.selectedItems()) == [0, 1, 2]
    sweep_move(queue, point(0))
    assert sorted(queue.row(item) for item in queue.selectedItems()) == [0, 1, 2]
    sweep_move(queue, point(3))
    assert sorted(queue.row(item) for item in queue.selectedItems()) == [0, 1, 2, 3]
    QTest.mouseRelease(queue.viewport(), Qt.MouseButton.LeftButton, pos=point(3))


def test_deselection_sweep_backtrack_restores_checks(queue):
    queue.set_check_mode(True)
    queue.clearSelection()
    for index in range(4):
        queue.item(index).setSelected(True)
    def point(row):
        return queue.check_rect(queue.item(row)).center()
    QTest.mousePress(queue.viewport(), Qt.MouseButton.LeftButton, pos=point(2))
    sweep_move(queue, point(0))
    assert [queue.row(item) for item in queue.selectedItems()] == [3]
    sweep_move(queue, point(1))
    assert sorted(queue.row(item) for item in queue.selectedItems()) == [0, 3]
    sweep_move(queue, point(3))
    assert sorted(queue.row(item) for item in queue.selectedItems()) == [0, 1]
    QTest.mouseRelease(queue.viewport(), Qt.MouseButton.LeftButton, pos=point(3))
