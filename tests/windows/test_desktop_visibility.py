"""Own-window native visibility checks; no devices, services or user text."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows startup window state')
@pytest.mark.parametrize('initial_show', [True, False])
def test_hidden_startup_tray_action_restores_native_window(tmp_path, initial_show):
    script = '''
import ctypes, json, sys
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon
from core.settings_gui.shell import install_tray
kernel = ctypes.WinDLL('kernel32')
kernel.SetErrorMode(kernel.GetErrorMode() | 3)
user = ctypes.WinDLL('user32')
user.IsWindowVisible.argtypes = [ctypes.c_void_p]
app = QApplication([])
window = QMainWindow()
window.setWindowTitle('CapsWriter synthetic visibility test')
window.resize(400, 200)
window.home_action = lambda name: None
window.request_exit = app.quit
QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
QSystemTrayIcon.show = lambda self: None
tray = install_tray(window, True)
observed = {}
def visible():
    return bool(user.IsWindowVisible(int(window.winId())))
def exercise():
    observed['before'] = {'qt': window.isVisible(), 'native': visible()}
    tray.contextMenu().actions()[0].trigger()
    observed['after'] = {'qt': window.isVisible(), 'native': visible()}
    window.hide()
    tray.contextMenu().actions()[0].trigger()
    observed['reopened'] = visible()
    window.showMinimized()
    tray.contextMenu().actions()[0].trigger()
    observed['restored'] = visible() and not window.isMinimized()
    window.showMaximized()
    tray.contextMenu().actions()[0].trigger()
    observed['maximized'] = visible() and window.isMaximized()
    Path(sys.argv[1]).write_text(json.dumps(observed))
    tray.hide()
    app.quit()
if INITIAL_SHOW:
    window.show()
QTimer.singleShot(200, exercise)
app.exec()
'''.replace('INITIAL_SHOW', str(initial_show))
    probe = tmp_path / 'visibility_probe.py'
    output = tmp_path / 'visibility.json'
    probe.write_text(script, encoding='utf-8')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    environment = dict(os.environ, QT_QPA_PLATFORM='windows',
                       PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    result = subprocess.run([str(Path(sys.executable).with_name('pythonw.exe')), str(probe), str(output)],
                            cwd=tmp_path, env=environment, startupinfo=startup,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    observed = json.loads(output.read_text())
    assert observed['before'] == {'qt': initial_show, 'native': False}
    assert observed['after'] == {'qt': True, 'native': True}
    assert observed['reopened'] and observed['restored'] and observed['maximized']
