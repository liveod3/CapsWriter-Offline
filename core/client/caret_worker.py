"""Access COM/UIA only in an isolated subprocess, without clipboard or key input."""

from __future__ import annotations
import json
import sys


CAPTURE_STATUSES = frozenset({
    "captured", "empty", "focus_changed", "password", "disabled_control",
    "self_process", "unsupported_control", "unsupported_text_pattern",
    "inactive_caret", "readonly_or_unknown", "selection", "caret_mismatch",
    "provider_error",
})
CAPTURE_METHODS = frozenset({"none", "text_pattern2", "text_pattern"})
CAPTURE_REASONS = frozenset({
    "none", "range_not_collapsed", "caret_selection_disagree",
    "caret_before_control", "caret_after_control", "selection_changed",
})
# Only unsupported-interface/method errors permit the collapsed-selection fallback.
UNSUPPORTED_HRESULTS = {0x80004002, 0x80004001, 0x80040204}


def read_context(expected: int, before: int, after: int) -> dict[str, str]:
    """Return bounded text and controlled metadata; never expose provider exceptions."""
    try:
        return _read_context(expected, max(0, min(2000, before)), max(0, min(1000, after)))
    except Exception:
        return {"status": "provider_error", "method": "none"}


def _read_context(expected: int, before: int, after: int) -> dict[str, str]:
    from .caret_context import foreground_window

    method = "none"

    def omitted(status, reason="none"):
        value = {"status": status, "method": method}
        if reason != "none":
            value["reason"] = reason
        return value

    if not expected or foreground_window() != expected:
        return omitted("focus_changed")
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    try:
        module = comtypes.client.GetModule("UIAutomationCore.dll")
        automation = comtypes.client.CreateObject(
            module.CUIAutomation, interface=module.IUIAutomation
        )
        element = automation.GetFocusedElement()
        if element.CurrentIsPassword != False:
            return omitted("password")
        if not element.CurrentIsEnabled:
            return omitted("disabled_control")
        if element.CurrentProcessId == __import__("os").getpid():
            return omitted("self_process")
        # Accept editable controls only; do not capture whole pages or documents.
        if element.CurrentControlType not in (50004, 50030):
            return omitted("unsupported_control")
        if not element.CurrentHasKeyboardFocus:
            return omitted("inactive_caret")
        try:
            unknown = element.GetCurrentPattern(10014)
            if not unknown:
                return omitted("unsupported_text_pattern")
            text_pattern = unknown.QueryInterface(module.IUIAutomationTextPattern)
        except comtypes.COMError as exc:
            if exc.hresult & 0xFFFFFFFF not in UNSUPPORTED_HRESULTS:
                raise
            return omitted("unsupported_text_pattern")
        ranges = text_pattern.GetSelection()
        # Omit context for selections instead of guessing replacement boundaries.
        if ranges.Length != 1:
            return omitted("selection")
        selection = ranges.GetElement(0)
        if selection.CompareEndpoints(0, selection, 1) != 0:
            return omitted("selection")
        caret = None
        try:
            unknown = element.GetCurrentPattern(10024)
            if unknown:
                pattern = unknown.QueryInterface(module.IUIAutomationTextPattern2)
                active, candidate = pattern.GetCaretRange()
                if active:
                    method = "text_pattern2"
                    if not candidate:
                        return omitted("provider_error")
                    caret = candidate
                # Some focused editable providers report an inactive Pattern2 caret.
                # Never use that range; independently validate the focused selection.
        except comtypes.COMError as exc:
            if exc.hresult & 0xFFFFFFFF not in UNSUPPORTED_HRESULTS:
                raise
        if caret is None:
            # UIA defines a single degenerate selection as the insertion point.
            # This reads no selected text and does not alter the control's selection.
            caret = selection
            method = "text_pattern"
        if caret.CompareEndpoints(0, caret, 1) != 0:
            return omitted("caret_mismatch", "range_not_collapsed")
        if caret.CompareEndpoints(0, selection, 0) != 0:
            return omitted("caret_mismatch", "caret_selection_disagree")
        # Document controls can be read-only pages; reject unsupported properties too.
        if caret.GetAttributeValue(module.UIA_IsReadOnlyAttributeId) != False:
            return omitted("readonly_or_unknown")
        document = text_pattern.DocumentRange
        if caret.CompareEndpoints(0, document, 0) < 0:
            return omitted("caret_mismatch", "caret_before_control")
        if caret.CompareEndpoints(1, document, 1) > 0:
            return omitted("caret_mismatch", "caret_after_control")
        left, right = caret.Clone(), caret.Clone()
        left.MoveEndpointByUnit(0, 0, -before)
        right.MoveEndpointByUnit(1, 0, after)
        # A provider range can traverse a larger accessibility tree. Stay inside
        # this focused control, even if nearby page text is reachable from it.
        if left.CompareEndpoints(0, document, 0) < 0:
            left.MoveEndpointByRange(0, document, 0)
        if right.CompareEndpoints(1, document, 1) > 0:
            right.MoveEndpointByRange(1, document, 1)
        value = {
            "before": left.GetText(before)[-before:] if before else "",
            "after": right.GetText(after)[:after] if after else "",
        }
        focused_now = automation.GetFocusedElement()
        if (not automation.CompareElements(element, focused_now)
                or not focused_now.CurrentHasKeyboardFocus
                or foreground_window() != expected):
            return omitted("focus_changed")
        # A selection made during the provider calls must not become usable context.
        current_ranges = text_pattern.GetSelection()
        if current_ranges.Length != 1:
            return omitted("selection")
        current_selection = current_ranges.GetElement(0)
        if current_selection.CompareEndpoints(0, current_selection, 1) != 0:
            return omitted("selection")
        if current_selection.CompareEndpoints(0, selection, 0) != 0:
            return omitted("caret_mismatch", "selection_changed")
        value.update(status="captured" if value["before"] or value["after"] else "empty",
                     method=method)
        return value
    finally:
        comtypes.CoUninitialize()


def main(arguments: list[str]) -> int:
    try:
        expected, before, after = map(int, arguments)
        value = read_context(expected, max(0, min(2000, before)), max(0, min(1000, after)))
    except Exception:
        value = {"status": "provider_error", "method": "none"}
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    return 0
