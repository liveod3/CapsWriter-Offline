"""Access COM/UIA only in an isolated subprocess, without clipboard or key input."""

from __future__ import annotations
import json
import sys


def read_context(expected: int, before: int, after: int) -> dict[str, str]:
    from .caret_context import foreground_window

    if foreground_window() != expected:
        return {}
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    try:
        module = comtypes.client.GetModule("UIAutomationCore.dll")
        automation = comtypes.client.CreateObject(
            module.CUIAutomation, interface=module.IUIAutomation
        )
        element = automation.GetFocusedElement()
        if element.CurrentIsPassword or not element.CurrentIsEnabled:
            return {}
        if element.CurrentProcessId == __import__("os").getpid():
            return {}
        # Accept editable controls only; do not capture whole pages or documents.
        if element.CurrentControlType not in (50004, 50030):
            return {}
        pattern = element.GetCurrentPattern(10024).QueryInterface(module.IUIAutomationTextPattern2)
        active, caret = pattern.GetCaretRange()
        if not active:
            return {}
        # Document controls can be read-only pages; reject unsupported properties too.
        if caret.GetAttributeValue(module.UIA_IsReadOnlyAttributeId) != False:
            return {}
        text_pattern = element.GetCurrentPattern(10014).QueryInterface(
            module.IUIAutomationTextPattern
        )
        ranges = text_pattern.GetSelection()
        # Omit context for selections instead of guessing replacement boundaries.
        if ranges.Length != 1:
            return {}
        selection = ranges.GetElement(0)
        if selection.CompareEndpoints(0, selection, 1) != 0:
            return {}
        left, right = caret.Clone(), caret.Clone()
        left.MoveEndpointByUnit(0, 0, -before)
        right.MoveEndpointByUnit(1, 0, after)
        value = {
            "before": left.GetText(before) if before else "",
            "after": right.GetText(after) if after else "",
        }
        focused_now = automation.GetFocusedElement()
        if not automation.CompareElements(element, focused_now) or foreground_window() != expected:
            return {}
        return value
    finally:
        comtypes.CoUninitialize()


def main(arguments: list[str]) -> int:
    try:
        expected, before, after = map(int, arguments)
        value = read_context(expected, max(0, min(2000, before)), max(0, min(1000, after)))
    except Exception:
        value = {}
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    return 0
