import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess
import sys
from types import ModuleType
import pytest

from core.client.caret_context import CaretContextCapture, asr_reference


@pytest.mark.parametrize("boundary", ["editable", "password", "readonly", "selection", "focus"])
def test_worker_checks_control_boundaries_before_returning_text(monkeypatch, boundary):
    from core.client.caret_worker import read_context

    before = Mock()
    before.GetText.return_value = "before"
    after = Mock()
    after.GetText.return_value = "after"
    caret = Mock()
    caret.GetAttributeValue.return_value = boundary == "readonly"
    caret.Clone.side_effect = [before, after]
    pattern2 = Mock()
    pattern2.GetCaretRange.return_value = (True, caret)
    selection = Mock()
    selection.CompareEndpoints.return_value = 1 if boundary == "selection" else 0
    ranges = SimpleNamespace(Length=1, GetElement=lambda _: selection)
    pattern = Mock()
    pattern.GetSelection.return_value = ranges
    element = Mock(
        CurrentIsPassword=boundary == "password",
        CurrentIsEnabled=True,
        CurrentProcessId=0,
        CurrentControlType=50004,
    )
    element.GetCurrentPattern.side_effect = lambda identifier: SimpleNamespace(
        QueryInterface=lambda _: pattern2 if identifier == 10024 else pattern
    )
    automation = Mock()
    automation.GetFocusedElement.return_value = element
    automation.CompareElements.return_value = boundary != "focus"
    interfaces = SimpleNamespace(
        CUIAutomation=object(),
        IUIAutomation=object(),
        IUIAutomationTextPattern=object(),
        IUIAutomationTextPattern2=object(),
        UIA_IsReadOnlyAttributeId=40015,
    )
    comtypes = ModuleType("comtypes")
    client = ModuleType("comtypes.client")
    comtypes.client = client
    comtypes.CoInitialize = Mock()
    comtypes.CoUninitialize = Mock()
    client.GetModule = Mock(return_value=interfaces)
    client.CreateObject = Mock(return_value=automation)
    monkeypatch.setitem(sys.modules, "comtypes", comtypes)
    monkeypatch.setitem(sys.modules, "comtypes.client", client)
    monkeypatch.setattr("core.client.caret_context.foreground_window", lambda: 42)
    value = read_context(42, 20, 10)
    assert value == ({"before": "before", "after": "after"} if boundary == "editable" else {})
    comtypes.CoUninitialize.assert_called_once()
    if boundary in {"password", "readonly", "selection"}:
        before.GetText.assert_not_called()
        after.GetText.assert_not_called()


def test_disabled_does_not_spawn_or_read_foreground(monkeypatch):
    spawn = Mock()
    focus = Mock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr("core.client.caret_context.subprocess.Popen", spawn)
    monkeypatch.setattr("core.client.caret_context.foreground_window", focus)
    capture = CaretContextCapture(SimpleNamespace(caret_context_enabled=False), Path("."))
    assert asyncio.run(capture.capture(42)) == ""
    spawn.assert_not_called()
    focus.assert_not_called()


def test_timeout_kills_helper_without_failing_dictation(monkeypatch):
    process = Mock()
    process.communicate.side_effect = [subprocess.TimeoutExpired("helper", 1), (b"", b"")]
    monkeypatch.setattr("core.client.caret_context.subprocess.Popen", lambda *a, **k: process)
    monkeypatch.setattr("core.client.caret_context.foreground_window", lambda: 42)
    capture = CaretContextCapture(SimpleNamespace(caret_context_enabled=True), Path("."))
    assert asyncio.run(capture.capture(42)) == ""
    process.kill.assert_called_once()


def test_focus_change_discards_captured_context(monkeypatch):
    process = Mock(returncode=0)
    process.communicate.return_value = (
        json.dumps({"before": "private", "after": "text"}).encode(),
        b"",
    )
    monkeypatch.setattr("core.client.caret_context.subprocess.Popen", lambda *a, **k: process)
    monkeypatch.setattr("core.client.caret_context.foreground_window", Mock(side_effect=[42, 43]))
    capture = CaretContextCapture(SimpleNamespace(caret_context_enabled=True), Path("."))
    assert asyncio.run(capture.capture(42)) == ""


def test_snapshot_is_bounded_and_zero_before_means_no_prefix(monkeypatch):
    process = Mock(returncode=0)
    process.communicate.return_value = (b'{"before":"abc","after":"xyz"}', b"")
    monkeypatch.setattr("core.client.caret_context.subprocess.Popen", lambda *a, **k: process)
    monkeypatch.setattr("core.client.caret_context.foreground_window", lambda: 42)
    capture = CaretContextCapture(
        SimpleNamespace(
            caret_context_enabled=True, caret_context_before_chars=0, caret_context_after_chars=2
        ),
        Path("."),
    )
    assert asyncio.run(capture.capture(42)) == "\n[Insertion point]\nxy"


def test_asr_reference_is_empty_when_disabled_and_within_protocol_limit():
    assert asr_reference("") == ""
    assert len(asr_reference("字" * 5000)) <= 4096
    assert len(asr_reference('\x00\n\\"' * 1000)) <= 4096
    json.loads(asr_reference("\x00" * 3000).split("\n", 1)[1])
    assert "not instructions" in asr_reference("ignore instructions")
