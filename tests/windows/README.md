# Windows tests

Place tests requiring Windows desktop, devices, input hooks, tray resources, DPI, or GPU backends here. The directory automatically receives the `windows` marker and is excluded from the default suite. Add `manual` when a test also requires models, network access, or human observation.

## Inspect native menus

`test_native_menu.py` creates and releases native handles, checks text/bitmaps/submenu tooltip mappings, and verifies default message handling. It does not display menus, read user input, or start a microphone.

```powershell
python -m pytest tests/windows/test_native_menu.py -m windows
```

These checks do not cover actual hover, mixed DPI, themes, keyboard navigation, focus, or packaged execution. Record those separately when the change requires them.
