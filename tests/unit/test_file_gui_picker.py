"""Keep picker cancellation and errors isolated from queue import."""

import pytest
from PySide6.QtWidgets import QFileDialog

from core.file_gui.picker import choose_paths
from core.i18n import Notice


@pytest.mark.parametrize('paths', [[], ['C:/Example/one.wav', 'C:/Example/two.mp4']])
def test_native_file_selection_and_cancel(monkeypatch, paths):
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *args: (paths, ''))
    assert choose_paths(None, {'.wav', '.mp4'}) == paths


def test_picker_failure_is_a_controlled_notice(monkeypatch):
    def unavailable(*args):
        raise OSError('synthetic native dialog failure')
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', unavailable)
    with pytest.raises(RuntimeError) as error:
        choose_paths(None, {'.wav'})
    assert isinstance(error.value.args[0], Notice)
