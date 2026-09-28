"""Qt desktop presence and tray; activation uses a local, user-scoped pipe."""

import hashlib
from pathlib import Path
import sys

from PySide6.QtCore import QLockFile, QStandardPaths, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from core.i18n import tr


class DesktopPresence:
    def __init__(self, root):
        self.name = 'capswriter-desktop-' + hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:24]
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.lock = QLockFile(str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))
                                  / (self.name + '.lock')))
        self.lock.setStaleLockTime(0)

    def acquire(self):
        if self.lock.tryLock(0):
            if self.server.listen(self.name):
                return True
            self.lock.unlock()
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(500):
            from core.i18n import Notice
            raise RuntimeError(Notice('gui.desktop_unavailable'))
        socket.disconnectFromServer()
        return False

    def close(self):
        self.server.close()
        self.lock.unlock()

    def bind(self, window):
        def activate():
            while self.server.hasPendingConnections():
                socket = self.server.nextPendingConnection()
                socket.disconnectFromServer()
                socket.deleteLater()
            show_window(window)
        self.server.newConnection.connect(activate)
        if self.server.hasPendingConnections():
            activate()


def show_window(window):
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    if QApplication.platformName() == 'windows':
        import ctypes
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user.IsWindowVisible.restype = ctypes.c_int
        if not user.IsWindowVisible(int(window.winId())):
            # STARTF_USESHOWWINDOW/SW_HIDE can override the first native show
            # while Qt already considers the widget visible. Reset both states.
            window.hide()
            window.show()
    window.raise_()
    window.activateWindow()


def desktop_icon(recording=False):
    root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
    icon = QIcon(str(root / 'assets' / 'client-icon.ico'))
    if not recording or icon.isNull():
        return icon
    pixmap = icon.pixmap(64, 64)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    size = pixmap.deviceIndependentSize()
    painter.scale(size.width() / 64, size.height() / 64)
    painter.setPen(Qt.PenStyle.NoPen)
    # Match the console client's recording badge, including its light outline.
    painter.setBrush(QColor(255, 248, 240))
    painter.drawEllipse(42, 42, 20, 20)
    painter.setBrush(QColor(255, 59, 48))
    painter.drawEllipse(44, 44, 16, 16)
    painter.end()
    return QIcon(pixmap)


def install_tray(window, enabled):
    if not enabled or not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(desktop_icon(), window)
    tray.setToolTip('CapsWriter')
    menu = QMenu(window)
    for key, callback in (
        ('show_window', lambda: show_window(window)),
        ('toggle_pause', lambda: window.home_action('toggle_pause')),
        ('exit_client', window.request_exit),
    ):
        action = QAction(tr('gui.' + key), menu)
        action.triggered.connect(callback)
        menu.addAction(action)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: show_window(window) if reason in (
        QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
    tray.show()
    return tray
