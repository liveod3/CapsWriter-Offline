"""Early desktop entry point, independent of audio, Tk and executable settings."""

from pathlib import Path
import sys

from core.i18n import set_language
from .backend import Backend


def main(root=None, *, child=False, desktop=False):
    from PySide6.QtWidgets import QApplication
    from .bridge import PipeBackend
    from .window import SettingsWindow

    root = Path(root) if root else (Path.cwd() if child else Path(sys.executable).parent if getattr(sys, 'frozen', False)
                                  else Path(__file__).resolve().parents[2])
    local = Backend(root)
    try:
        set_language(local.config().get('ui_language', 'auto'))
    except (OSError, ValueError):
        set_language('auto')
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('CapsWriter')
    presence = None
    if desktop:
        from PySide6.QtWidgets import QMessageBox
        from .shell import DesktopPresence
        from .desktop import DesktopBackend
        from .backend import safe_error
        presence = DesktopPresence(root)
        try:
            if not presence.acquire():
                return 0
        except RuntimeError as exc:
            QMessageBox.warning(None, 'CapsWriter', safe_error(exc))
            return 2
        backend = DesktopBackend(root)
    else:
        backend = PipeBackend(sys.stdin.buffer, sys.stdout.buffer) if child else local
    window = SettingsWindow(backend)
    from .shell import desktop_icon
    window.setWindowIcon(desktop_icon())
    window.desktop_mode = desktop
    window.exit_button.setVisible(desktop)
    if desktop:
        window.home.update_snapshot({'desktop': {'phase': 'starting'}})
        from .shell import install_tray, show_window
        app.setQuitOnLastWindowClosed(False)
        try:
            preference = local.config()
        except (OSError, ValueError):
            preference = {}
        window.tray = install_tray(window, preference.get('enable_tray', True))
        presence.bind(window)
        if not (preference.get('start_minimized', False) and window.tray):
            show_window(window)
    else:
        window.show()
    try:
        return app.exec()
    finally:
        window.closed.set()
        window.device_watch.stop()
        window.timer.stop()
        window.poll.stop()
        if desktop:
            # Explicit exit normally completes this through the off-thread backend.
            # Also close the owned process on abnormal Qt loop termination.
            if backend.session:
                backend.session.close(graceful=False)
            presence.close()
        window.thread.join(timeout=1)
