"""
Foreground window detection.

Identify the active application for compatibility settings.
"""
import platform


def get_active_window_info() -> dict:
    """
    Get foreground window information.

    Returns:
        Dictionary fields:
        - title: Window title.
        - class_name: Window class.
        - process_name: Executable name.
        - app_name: Inferred application name.
    """
    system = platform.system()

    if system == 'Windows':
        return _get_windows_window_info()
    elif system == 'Darwin':  # macOS
        return _get_macos_window_info()
    elif system == 'Linux':
        return _get_linux_window_info()
    else:
        return {}


def _get_windows_window_info() -> dict:
    """Detect the foreground window on Windows."""
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()

        # Read the window title.
        title = win32gui.GetWindowText(hwnd)

        # Read the window class.
        class_name = win32gui.GetClassName(hwnd)

        # Read the process ID and executable name.
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            import psutil
            process = psutil.Process(pid)
            process_name = process.name()
        except:
            process_name = ""

        # Infer the application name.
        app_name = _guess_app_name(title, class_name, process_name)

        return {
            'title': title,
            'class_name': class_name,
            'process_name': process_name,
            'app_name': app_name
        }
    except ImportError:
        # Return empty information if dependencies are missing.
        return {}
    except Exception:
        return {}


def _get_macos_window_info() -> dict:
    """Detect the foreground window on macOS."""
    try:
        import subprocess
        from plistlib import loads

        # Query the foreground window through AppleScript.
        script = '''
        tell application "System Events"
            set frontApp to name of first application process whose frontmost is true
            if frontApp contains "Safari" then
                tell application frontApp
                    if (count of windows) > 0 then
                        set windowTitle to name of front window
                    else
                        set windowTitle to ""
                    end if
                end tell
            else if frontApp contains "Terminal" then
                tell application frontApp
                    if (count of windows) > 0 then
                        set windowTitle to name of front window
                    else
                        set windowTitle to ""
                    end if
                end tell
            else
                set windowTitle to ""
            end if
        end tell
        return frontApp & "||" & windowTitle
        '''

        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            parts = result.stdout.strip().split('||')
            app_name = parts[0] if len(parts) > 0 else ""
            title = parts[1] if len(parts) > 1 else ""

            return {
                'title': title,
                'class_name': '',
                'process_name': app_name,
                'app_name': app_name
            }
    except Exception:
        pass

    return {}


def _get_linux_window_info() -> dict:
    """Detect the foreground window on Linux."""
    try:
        import subprocess

        # Query the active window through wmctrl.
        result = subprocess.run(
            ['wmctrl', '-G', '-a', ':ACTIVE:'],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) >= 5:
                title = ' '.join(parts[5:])
                return {
                    'title': title,
                    'class_name': '',
                    'process_name': '',
                    'app_name': title.split()[0] if title else ''
                }
    except Exception:
        pass

    return {}


def _guess_app_name(title: str, class_name: str, process_name: str) -> str:
    """
    Infer an application name from window metadata.

    Args:
        title: Window title.
        class_name: Window class.
        process_name: Executable name.

    Returns:
        Inferred application name.
    """
    # Prefer the executable name.
    if process_name:
        # Remove the .exe suffix.
        name = process_name.replace('.exe', '').lower()
        return name

    # Fall back to the window class.
    if class_name:
        # Common Windows window classes.
        class_mappings = {
            'chrome': 'Chrome',
            'msedge': 'Edge',
            'firefox': 'Firefox',
            'notepad': 'Notepad',
            'notepad++': 'Notepad++',
            'vscode': 'VSCode',
            'winword': 'Word',
            'xlmain': 'Excel',
            'pptmain': 'PowerPoint',
            'wndclass_desktop_glass': 'Desktop',
        }

        class_lower = class_name.lower()
        for key, value in class_mappings.items():
            if key in class_lower:
                return value

    # Finally use the first word of the title.
    if title:
        first_word = title.split()[0]
        return first_word

    return ''


def is_likely_editor(window_info: dict) -> bool:
    """
    Return whether the foreground window appears to be an editor.

    Args:
        window_info: Window metadata.

    Returns:
        True if it appears to be an editor.
    """
    if not window_info:
        return True  # Use the conservative default.

    title = window_info.get('title', '').lower()
    class_name = window_info.get('class_name', '').lower()
    process_name = window_info.get('process_name', '').lower()

    # Editor keywords.
    editor_keywords = [
        'visual studio', 'vscode', 'vim', 'nano', 'emacs',
        'notepad', 'sublime', 'atom', 'intellij', 'pycharm',
        'webstorm', 'idea', 'editor'
    ]

    # Check the title.
    for keyword in editor_keywords:
        if keyword in title or keyword in class_name or keyword in process_name:
            return True

    return False


def is_likely_browser(window_info: dict) -> bool:
    """
    Return whether the foreground window appears to be a browser.

    Args:
        window_info: Window metadata.

    Returns:
        True if it appears to be a browser.
    """
    if not window_info:
        return False

    class_name = window_info.get('class_name', '').lower()
    process_name = window_info.get('process_name', '').lower()

    browser_keywords = ['chrome', 'firefox', 'edge', 'safari', 'opera', 'brave']

    for keyword in browser_keywords:
        if keyword in class_name or keyword in process_name:
            return True

    return False
