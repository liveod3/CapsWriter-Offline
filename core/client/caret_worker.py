"""仅在隔离子进程中访问 COM/UIA，不使用剪贴板或模拟按键。"""

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
        # 只允许可编辑文本控件；不要收集浏览器页面或整个文档的可见文字。
        if element.CurrentControlType not in (50004, 50030):
            return {}
        pattern = element.GetCurrentPattern(10024).QueryInterface(module.IUIAutomationTextPattern2)
        active, caret = pattern.GetCaretRange()
        if not active:
            return {}
        # Document 也可能是只读网页；属性不支持时同样不采集。
        if caret.GetAttributeValue(module.UIA_IsReadOnlyAttributeId) != False:
            return {}
        text_pattern = element.GetCurrentPattern(10014).QueryInterface(
            module.IUIAutomationTextPattern
        )
        ranges = text_pattern.GetSelection()
        # 有选区时不猜测替换边界，降级为无上下文。
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
