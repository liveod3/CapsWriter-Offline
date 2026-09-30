"""Windowed entry point for the independent file transcription workspace."""


def main():
    from core.file_gui.main import main as gui_main
    return gui_main()


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main())
