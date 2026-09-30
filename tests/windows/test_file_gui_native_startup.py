"""Inspect an owned file window on the interactive desktop without user media."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows interactive desktop required')
def test_file_window_starts_inside_interactive_primary_work_area(tmp_path):
    script = r'''
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from core.file_gui.runner import FileTranscriptionRunner
from core.file_gui.startup import show_initial_window
from core.file_gui.window import FileTranscriptionWindow

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
user = ctypes.WinDLL('user32', use_last_error=True)
kernel.GetCurrentThreadId.restype = wintypes.DWORD
user.GetThreadDesktop.argtypes = [wintypes.DWORD]
user.GetThreadDesktop.restype = wintypes.HANDLE
user.GetProcessWindowStation.restype = wintypes.HANDLE
user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
user.GetUserObjectInformationW.restype = wintypes.BOOL

def object_name(handle):
    name = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if not user.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return name.value

desktop = object_name(user.GetThreadDesktop(kernel.GetCurrentThreadId()))
station = object_name(user.GetProcessWindowStation())
output = Path(sys.argv[1])
observed = {'desktop': desktop, 'station': station}
if desktop != 'Default' or station != 'WinSta0':
    output.write_text(json.dumps(observed))
    raise SystemExit(0)

class MonitorInfo(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                ('work', wintypes.RECT), ('flags', wintypes.DWORD)]

user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user.GetWindowRect.restype = wintypes.BOOL
user.IsWindowVisible.argtypes = [wintypes.HWND]
user.IsWindowVisible.restype = wintypes.BOOL
user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user.MonitorFromWindow.restype = wintypes.HANDLE
user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
user.GetMonitorInfoW.restype = wintypes.BOOL
dwm = ctypes.WinDLL('dwmapi')
dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwm.DwmGetWindowAttribute.restype = ctypes.c_long

app = QApplication([])
runner = FileTranscriptionRunner(Path.cwd())
window = FileTranscriptionWindow(runner, {})
window.setWindowTitle('CapsWriter synthetic startup placement test')
# Reproduce an invalid initial position independently of the real monitor layout.
window.move(-100000, -100000)
show_initial_window(window)

def sample():
    hwnd = int(window.winId())
    rect = wintypes.RECT()
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    assert user.GetWindowRect(hwnd, ctypes.byref(rect))
    assert user.GetMonitorInfoW(user.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    cloaked = wintypes.DWORD()
    assert dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0
    observed.update(visible=bool(user.IsWindowVisible(hwnd)), cloaked=cloaked.value,
                    primary=bool(info.flags & 1),
                    frame=[rect.left, rect.top, rect.right, rect.bottom],
                    work=[info.work.left, info.work.top, info.work.right, info.work.bottom])
    output.write_text(json.dumps(observed))
    window.close()
    app.quit()

QTimer.singleShot(250, sample)
app.exec()
'''
    probe = tmp_path / 'file_startup_probe.py'
    output = tmp_path / 'placement.json'
    probe.write_text(script, encoding='utf-8')
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, QT_QPA_PLATFORM='windows', PYTHONPATH=str(root))
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    result = subprocess.run(
        [str(Path(sys.executable).with_name('pythonw.exe')), str(probe), str(output)],
        cwd=root, env=environment, startupinfo=startup, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(output.read_text())
    if (observed['station'], observed['desktop']) != ('WinSta0', 'Default'):
        pytest.skip('Visibility proof requires the approved interactive WinSta0/Default desktop')
    assert observed['visible'] and not observed['cloaked'] and observed['primary']
    left, top, right, bottom = observed['frame']
    work_left, work_top, work_right, work_bottom = observed['work']
    # Oversized minimum dimensions may exceed a small display, but the title bar
    # and the maximum possible frame area must still remain on that display.
    assert work_left <= left < work_right
    assert work_top <= top < work_bottom
    assert min(right, work_right) - left >= min(right - left, work_right - work_left) - 2
    assert min(bottom, work_bottom) - top >= min(bottom - top, work_bottom - work_top) - 2
