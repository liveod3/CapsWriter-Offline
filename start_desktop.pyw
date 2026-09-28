"""Windowed source launcher; CLI and server entry points remain available."""

def main():
    from core.settings_gui.main import main as gui_main
    return gui_main(desktop=True)


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main())
