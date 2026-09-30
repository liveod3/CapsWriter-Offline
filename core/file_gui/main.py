"""Start the standalone Qt workspace without microphone or shortcut ownership."""

from pathlib import Path

from core.i18n import set_language


def main(root=None):
    from PySide6.QtWidgets import QApplication
    from core.settings import SettingsService
    from core.settings_gui.shell import desktop_icon
    from .runner import FileTranscriptionRunner
    from .startup import show_initial_window
    from .window import FileTranscriptionWindow

    root = Path(root) if root else Path(__file__).resolve().parents[2]
    config = {}
    config_error = False
    try:
        snapshot = SettingsService.standalone(root / 'config_client.py').read()
        if snapshot.error:
            config_error = True
        else:
            config = dict(snapshot.saved['ClientConfig'])
    except (OSError, ValueError):
        config_error = True
    set_language(config.get('ui_language', 'auto'))
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('CapsWriter Files')
    runner = FileTranscriptionRunner(root)
    window = FileTranscriptionWindow(runner, config)
    window.setWindowIcon(desktop_icon())
    if config_error:
        window.set_notice('config_error')
    app.aboutToQuit.connect(runner.shutdown)
    show_initial_window(window)
    return app.exec()
