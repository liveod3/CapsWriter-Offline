"""Verify styled plain-text events and scroll ownership without desktop input."""

from datetime import datetime
import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope='module')
def qt_app():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    return QApplication.instance() or QApplication([])


@pytest.fixture
def log(qt_app, monkeypatch):
    from core.file_gui import log_view
    from core.settings_gui.presentation import apply_theme

    def clipboard_forbidden():
        pytest.fail('Event display must not read clipboard data')

    monkeypatch.setattr(QApplication, 'clipboard', clipboard_forbidden)
    monkeypatch.setattr(log_view, 'datetime', SimpleNamespace(now=lambda: datetime(2026, 9, 30, 12, 34, 56)))
    widget = log_view.FileEventLog()
    apply_theme(widget)
    widget.resize(560, 240)
    widget.show()
    qt_app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qt_app.processEvents()


def add_lines(log, count, *, start=0):
    log.append('\n'.join(f'Synthetic event {index:04}' for index in range(start, start + count)))


def visible_anchor(log):
    cursor = log.editor.cursorForPosition(QPoint(0, 0))
    cursor.setPosition(cursor.block().position())
    return cursor.block().text(), log.editor.cursorRect(cursor).top()


def body_format(log, block_number):
    block = log.editor.document().findBlockByNumber(block_number)
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + len('[12:34:56] ') + 1)
    return cursor.charFormat()


def test_events_remain_plain_text_with_timestamps_and_safe_controls(log):
    log.append('<b>Synthetic & event</b>\nC:\\Media\\second.wav\tready\x00\u202e')
    assert log.editor.isReadOnly()
    assert not log.editor.acceptRichText()
    assert not log.editor.isUndoRedoEnabled()
    assert log.editor.toPlainText().splitlines() == [
        '[12:34:56] <b>Synthetic & event</b>',
        '[12:34:56] C:\\Media\\second.wav    ready\\u0000\\u202e',
    ]


def test_levels_have_distinct_colors_and_markup_stays_literal(log):
    from core.file_gui.log_view import LEVEL_COLORS

    for level in LEVEL_COLORS:
        log.append(f'<b>{level}</b> & literal text', level)
    for index, (level, color) in enumerate(LEVEL_COLORS.items()):
        assert body_format(log, index).foreground().color().name() == color
        expected_weight = QFont.Weight.DemiBold if level in {'success', 'warning', 'error'} else QFont.Weight.Normal
        assert body_format(log, index).fontWeight() == expected_weight
    assert len(set(LEVEL_COLORS.values())) == len(LEVEL_COLORS)
    assert '<b>warning</b> & literal text' in log.editor.toPlainText()
    log.append('Unknown level falls back safely', 'unsupported')
    assert body_format(log, len(LEVEL_COLORS)).foreground().color().name() == LEVEL_COLORS['info']


def test_empty_events_do_not_create_timestamp_only_rows(log):
    log.append(' \n\t')
    assert log.editor.toPlainText() == ''


def test_lines_and_retained_history_are_bounded(log):
    from core.file_gui.log_view import MAX_BLOCKS, MAX_LINE_CHARS
    log.append('x' * (MAX_LINE_CHARS + 100))
    assert len(log.editor.toPlainText()) == MAX_LINE_CHARS
    assert log.editor.toPlainText().endswith('…')
    add_lines(log, MAX_BLOCKS)
    log.append('Newest event')
    text = log.editor.toPlainText().splitlines()
    assert log.editor.document().blockCount() == MAX_BLOCKS
    assert len(text) == MAX_BLOCKS
    assert text[0].endswith('Synthetic event 0001')
    assert text[-1].endswith('Newest event')


def test_retained_styles_survive_history_trimming(log):
    from core.file_gui.log_view import LEVEL_COLORS, MAX_BLOCKS

    add_lines(log, MAX_BLOCKS - 1)
    log.append('Existing output gets a numbered suffix', 'warning')
    log.append('Saved C:\\Synthetic\\recording_2.txt', 'output')
    assert body_format(log, MAX_BLOCKS - 2).foreground().color().name() == LEVEL_COLORS['warning']
    assert body_format(log, MAX_BLOCKS - 1).foreground().color().name() == LEVEL_COLORS['output']


def test_follow_scrolls_to_new_events_until_user_reads_earlier_lines(log, qt_app):
    add_lines(log, 100)
    qt_app.processEvents()
    scrollbar = log.editor.verticalScrollBar()
    assert scrollbar.maximum() > 0
    assert log.follow.isChecked() and scrollbar.value() == scrollbar.maximum()
    scrollbar.setValue(35)
    assert not log.follow.isChecked()
    anchor = visible_anchor(log)
    previous = scrollbar.value()
    add_lines(log, 10, start=100)
    qt_app.processEvents()
    assert scrollbar.value() == previous
    assert visible_anchor(log) == anchor
    assert not log.follow.isChecked()


def test_bounded_trim_preserves_the_visible_event_when_follow_is_paused(log, qt_app):
    from core.file_gui.log_view import MAX_BLOCKS
    add_lines(log, MAX_BLOCKS)
    qt_app.processEvents()
    scrollbar = log.editor.verticalScrollBar()
    scrollbar.setValue(600)
    anchor = visible_anchor(log)
    add_lines(log, 10, start=MAX_BLOCKS)
    qt_app.processEvents()
    assert not log.follow.isChecked()
    assert scrollbar.value() < 600
    assert visible_anchor(log) == anchor


def test_expired_visible_event_clamps_to_oldest_retained_line(log, qt_app):
    from core.file_gui.log_view import MAX_BLOCKS
    add_lines(log, MAX_BLOCKS)
    qt_app.processEvents()
    scrollbar = log.editor.verticalScrollBar()
    scrollbar.setValue(5)
    add_lines(log, 10, start=MAX_BLOCKS)
    qt_app.processEvents()
    assert scrollbar.value() == 0
    assert visible_anchor(log)[0].endswith('Synthetic event 0010')
    assert not log.follow.isChecked()


def test_explicit_follow_resumes_bottom_once_and_clear_retains_preference(log, qt_app):
    add_lines(log, 100)
    qt_app.processEvents()
    scrollbar = log.editor.verticalScrollBar()
    scrollbar.setValue(20)
    changes = []
    scrollbar.valueChanged.connect(changes.append)
    log.follow.setChecked(True)
    assert changes == [scrollbar.maximum()]
    assert scrollbar.value() == scrollbar.maximum()
    log.append('Latest event')
    qt_app.processEvents()
    assert log.follow.isChecked() and scrollbar.value() == scrollbar.maximum()
    log.follow.setChecked(False)
    log.clear()
    assert log.editor.toPlainText() == ''
    assert scrollbar.value() == 0
    assert not log.follow.isChecked()


@pytest.mark.parametrize('answer', [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
def test_clear_button_asks_confirmation_with_no_as_default(log, monkeypatch, answer):
    from core.file_gui import log_view

    calls = []

    def execute(confirmation):
        calls.append(confirmation)
        assert confirmation.parent() is log
        assert confirmation.windowTitle() == log_view.tr('files.log_clear_confirm_title')
        assert confirmation.text() == log_view.tr('files.log_clear_confirm_body')
        assert confirmation.standardButtons() == QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        yes = confirmation.button(QMessageBox.StandardButton.Yes)
        no = confirmation.button(QMessageBox.StandardButton.No)
        assert yes.text() == log_view.tr('files.log_clear')
        assert no.text() == log_view.tr('files.cancel_settings')
        assert confirmation.defaultButton() is no
        assert confirmation.escapeButton() is no
        return answer

    monkeypatch.setattr(QMessageBox, 'exec', execute)
    assert not log.clear_button.isEnabled()
    log.clear_button.click()
    log.confirm_clear()
    assert calls == []
    log.append('Keep unless explicitly confirmed')
    original = log.editor.toPlainText()
    assert log.clear_button.isEnabled()
    log.clear_button.click()
    assert len(calls) == 1
    cleared = answer == QMessageBox.StandardButton.Yes
    assert log.editor.toPlainText() == ('' if cleared else original)
    assert log.clear_button.isEnabled() is not cleared


def test_programmatic_clear_never_asks_confirmation(log, monkeypatch):
    def confirmation_forbidden(*_args):
        pytest.fail('Programmatic clear must not show a prompt')

    monkeypatch.setattr(QMessageBox, 'exec', confirmation_forbidden)
    log.append('Synthetic event')
    log.clear()
    assert log.editor.toPlainText() == ''
    assert not log.clear_button.isEnabled()


@pytest.mark.parametrize('width', [320, 560])
def test_header_stays_uncluttered_and_collapsed_card_has_fixed_height(log, qt_app, width):
    from core.file_gui.log_view import COLLAPSED_HEIGHT

    log.resize(width, 260)
    qt_app.processEvents()
    assert log.title.objectName() == 'cardTitle'
    assert log.title.geometry().right() < log.collapse_button.geometry().left()
    assert abs(log.title.geometry().center().y() - log.collapse_button.geometry().center().y()) <= 1
    assert log.collapse_button.text() == ''
    assert log.collapse_button.width() == log.collapse_button.height() == 28
    assert log.controls.geometry().top() > log.editor.geometry().bottom()
    assert log.follow.geometry().right() < log.clear_button.geometry().left()
    log.collapse_button.click()
    qt_app.processEvents()
    assert log.height() == log.minimumHeight() == log.maximumHeight() == COLLAPSED_HEIGHT
    assert log.title.isVisible() and log.collapse_button.isVisible()
    assert not log.editor.isVisible() and not log.controls.isVisible()
    assert log.rect().contains(log.collapse_button.geometry())
    assert log.rect().contains(log.title.geometry())


def test_keyboard_disclosure_keeps_action_name_in_sync(log, qt_app):
    from PySide6.QtTest import QTest
    from core.i18n import tr

    toggles = []
    log.collapse_button.toggled.connect(toggles.append)
    log.collapse_button.setFocus()
    QTest.keyClick(log.collapse_button, Qt.Key.Key_Space)
    qt_app.processEvents()
    assert toggles == [True]
    assert log.collapse_button.toolTip() == log.collapse_button.accessibleName() == tr('files.log_expand')
    QTest.keyClick(log.collapse_button, Qt.Key.Key_Space)
    qt_app.processEvents()
    assert toggles == [True, False]
    assert log.collapse_button.toolTip() == log.collapse_button.accessibleName() == tr('files.log_collapse')
    assert log.editor.isVisible()


@pytest.mark.parametrize('following', [True, False])
def test_collapse_retains_history_and_follow_preference(log, qt_app, following):
    add_lines(log, 100)
    qt_app.processEvents()
    scrollbar = log.editor.verticalScrollBar()
    if not following:
        scrollbar.setValue(450)
    anchor = visible_anchor(log)
    original = log.editor.toPlainText()
    log.collapse_button.click()
    qt_app.processEvents()
    assert log.collapse_button.isChecked()
    assert log.collapse_button.arrowType() == Qt.ArrowType.NoArrow
    assert log.title.isVisible()
    assert log.height() == 52
    assert not log.editor.isVisible() and not log.controls.isVisible()
    assert log.maximumHeight() < 150
    assert log.editor.toPlainText() == original
    assert log.follow.isChecked() is following
    log.append('Event received while collapsed', 'progress')
    log.collapse_button.click()
    qt_app.processEvents()
    assert not log.collapse_button.isChecked()
    assert log.collapse_button.arrowType() == Qt.ArrowType.NoArrow
    assert log.editor.isVisible() and log.controls.isVisible()
    assert log.minimumHeight() == 150 and log.maximumHeight() > 150
    assert log.editor.toPlainText().startswith(original)
    assert log.editor.toPlainText().endswith('Event received while collapsed')
    assert log.follow.isChecked() is following
    if following:
        assert scrollbar.value() == scrollbar.maximum()
    else:
        assert visible_anchor(log) == anchor


def test_disabling_follow_at_bottom_holds_view_on_next_append(log, qt_app):
    add_lines(log, 100)
    qt_app.processEvents()
    scrollbar = log.editor.verticalScrollBar()
    previous = scrollbar.value()
    log.follow.setChecked(False)
    log.append('New event while following is disabled')
    qt_app.processEvents()
    assert scrollbar.value() == previous
    assert not log.follow.isChecked()


@pytest.mark.parametrize('level', ['output', 'warning', 'error'])
def test_long_paths_and_warnings_wrap_without_hiding_text_or_parsing_markup(log, qt_app, level):
    log.resize(400, 240)
    add_lines(log, 100)
    path = 'C:\\Synthetic\\' + 'unbrokenlongdirectoryname' * 20 + '\\sample.wav'
    message = '<b>Untrusted event</b> ' + path
    log.append(message, level)
    qt_app.processEvents()
    last_block = log.editor.document().lastBlock()
    assert last_block.text() == '[12:34:56] ' + message
    assert last_block.layout().lineCount() > 1
    assert log.editor.horizontalScrollBar().maximum() == 0
    for index in range(last_block.layout().lineCount()):
        assert last_block.layout().lineAt(index).naturalTextWidth() <= log.editor.viewport().width()
    assert log.follow.isChecked()
    vertical = log.editor.verticalScrollBar()
    assert vertical.value() == vertical.maximum()


def test_wrapped_lines_keep_paused_visible_anchor_when_old_blocks_are_trimmed(log, qt_app):
    from core.file_gui.log_view import MAX_BLOCKS

    log.resize(400, 240)
    log.append('\n'.join(
        f'Synthetic event {index:04} ' + 'LongDirectorySegment/' * (5 + index % 4)
        for index in range(MAX_BLOCKS)
    ))
    qt_app.processEvents()
    assert log.editor.document().firstBlock().layout().lineCount() > 1
    vertical = log.editor.verticalScrollBar()
    vertical.setValue(1807)
    anchor = visible_anchor(log)
    assert not log.follow.isChecked()
    assert log.editor.cursorForPosition(QPoint(0, 0)).blockNumber() > 10
    log.append('\n'.join('New wrapped warning ' + 'long word ' * 50 for _ in range(10)), 'warning')
    qt_app.processEvents()
    assert log.editor.document().blockCount() == MAX_BLOCKS
    assert visible_anchor(log) == anchor
    assert vertical.value() < 1807
    assert not log.follow.isChecked()
    assert log.editor.horizontalScrollBar().maximum() == 0
