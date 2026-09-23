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


class ProviderError(Exception):
    def __init__(self, hresult):
        self.hresult = hresult
        super().__init__("private provider content")


@pytest.fixture
def provider(monkeypatch):
    env = SimpleNamespace(text="leftRIGHT", readonly=False, reads=[], pattern2=True)

    class Range:
        def __init__(self, start=4, end=4):
            self.points = [start, end]

        def Clone(self):
            return Range(*self.points)

        def CompareEndpoints(self, endpoint, other, other_endpoint):
            return self.points[endpoint] - other.points[other_endpoint]

        def GetAttributeValue(self, attribute):
            return env.readonly

        def MoveEndpointByRange(self, endpoint, other, other_endpoint):
            self.points[endpoint] = other.points[other_endpoint]

        def MoveEndpointByUnit(self, endpoint, unit, count):
            assert unit == 0
            old = self.points[endpoint]
            self.points[endpoint] = max(0, min(len(env.text), old + count))
            return self.points[endpoint] - old

        def GetText(self, limit):
            env.reads.append((tuple(self.points), limit))
            return env.text[self.points[0]:self.points[1]][:limit]

    env.Range = Range
    env.selection = Range()
    env.caret = Range()
    env.ranges = SimpleNamespace(Length=1, GetElement=lambda _: env.selection)
    env.pattern = Mock()
    env.pattern.DocumentRange = Range(0, len(env.text))
    env.pattern.GetSelection.return_value = env.ranges
    env.pattern_two = Mock()
    env.pattern_two.GetCaretRange.return_value = (True, env.caret)
    env.element = Mock(CurrentIsPassword=False, CurrentIsEnabled=True,
                       CurrentHasKeyboardFocus=True, CurrentProcessId=0, CurrentControlType=50004)

    def get_pattern(identifier):
        if identifier == 10024:
            if env.pattern2 is None:
                return None
            if env.pattern2 is not True:
                raise ProviderError(env.pattern2)
        return SimpleNamespace(QueryInterface=lambda _: (
            env.pattern_two if identifier == 10024 else env.pattern))

    env.element.GetCurrentPattern.side_effect = get_pattern
    env.automation = Mock()
    env.automation.GetFocusedElement.return_value = env.element
    env.automation.CompareElements.return_value = True
    interfaces = SimpleNamespace(
        CUIAutomation=object(), IUIAutomation=object(), IUIAutomationTextPattern=object(),
        IUIAutomationTextPattern2=object(), UIA_IsReadOnlyAttributeId=40015,
    )
    env.comtypes = ModuleType("comtypes")
    client = ModuleType("comtypes.client")
    env.comtypes.client = client
    env.comtypes.COMError = ProviderError
    env.comtypes.CoInitialize = Mock()
    env.comtypes.CoUninitialize = Mock()
    client.GetModule = Mock(return_value=interfaces)
    client.CreateObject = Mock(return_value=env.automation)
    monkeypatch.setitem(sys.modules, "comtypes", env.comtypes)
    monkeypatch.setitem(sys.modules, "comtypes.client", client)
    monkeypatch.setattr("core.client.caret_context.foreground_window", lambda: 42)
    return env


@pytest.mark.parametrize("pattern2", [True, None, -2147467262, -2147467263, -2147220988])
def test_worker_supports_caret_and_collapsed_selection(provider, pattern2):
    from core.client.caret_worker import read_context

    provider.pattern2 = pattern2
    assert read_context(42, 2, 3) == {
        "before": "ft", "after": "RIG", "status": "captured",
        "method": "text_pattern2" if pattern2 is True else "text_pattern",
    }
    assert provider.selection.points == provider.caret.points == [4, 4]
    provider.comtypes.CoUninitialize.assert_called_once()


@pytest.mark.parametrize("pattern2", [True, None])
@pytest.mark.parametrize("boundary,status", [
    ("password", "password"), ("disabled", "disabled_control"),
    ("readonly", "readonly_or_unknown"), ("unknown_readonly", "readonly_or_unknown"),
    ("selection", "selection"), ("multiple", "selection"), ("no_selection", "selection"),
    ("unfocused", "inactive_caret"), ("control_type", "unsupported_control"),
    ("self", "self_process"),
])
def test_worker_rejects_unsafe_controls_without_reading(provider, pattern2, boundary, status):
    from core.client.caret_worker import read_context

    provider.pattern2 = pattern2
    if boundary == "password":
        provider.element.CurrentIsPassword = True
    elif boundary == "disabled":
        provider.element.CurrentIsEnabled = False
    elif boundary in {"readonly", "unknown_readonly"}:
        provider.readonly = True if boundary == "readonly" else object()
    elif boundary == "selection":
        provider.selection.points[1] = 6
    elif boundary in {"multiple", "no_selection"}:
        provider.ranges.Length = 2 if boundary == "multiple" else 0
    elif boundary == "unfocused":
        provider.element.CurrentHasKeyboardFocus = False
    elif boundary == "control_type":
        provider.element.CurrentControlType = 50020
    elif boundary == "self":
        provider.element.CurrentProcessId = __import__("os").getpid()
    result = read_context(42, 20, 10)
    assert result["status"] == status and "before" not in result
    assert not provider.reads


@pytest.mark.parametrize("failure", ["mismatch", "not_collapsed", "provider_error"])
def test_pattern2_rejections_do_not_fall_back(provider, failure):
    from core.client.caret_worker import read_context

    if failure == "mismatch":
        provider.caret.points = [3, 3]
    elif failure == "not_collapsed":
        provider.caret.points = [3, 4]
    else:
        provider.pattern2 = -2147024891  # E_ACCESSDENIED is not an unsupported pattern.
    result = read_context(42, 20, 10)
    assert result["status"] in {"inactive_caret", "caret_mismatch", "provider_error"}
    assert result["method"] != "text_pattern" and not provider.reads
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("boundary", ["editable", "unfocused", "readonly", "selection", "outside"])
def test_inactive_pattern2_uses_only_a_safe_focused_selection(provider, boundary):
    from core.client.caret_worker import read_context

    # The inactive range is stale and must never be used as the text source.
    provider.pattern_two.GetCaretRange.return_value = (False, provider.Range(1, 1))
    if boundary == "unfocused":
        provider.element.CurrentHasKeyboardFocus = False
    elif boundary == "readonly":
        provider.readonly = True
    elif boundary == "selection":
        provider.selection.points = [3, 5]
    elif boundary == "outside":
        provider.pattern.DocumentRange = provider.Range(5, 9)
    result = read_context(42, 20, 10)
    if boundary == "editable":
        assert result == {"before": "left", "after": "RIGHT",
                          "status": "captured", "method": "text_pattern"}
    else:
        assert result["status"] != "captured" and not provider.reads


@pytest.mark.parametrize("pattern2", [True, None])
def test_expanded_ranges_stay_inside_focused_editor(provider, pattern2):
    from core.client.caret_worker import read_context

    provider.pattern2 = pattern2
    provider.pattern.DocumentRange = provider.Range(2, 6)
    result = read_context(42, 20, 10)
    assert result["before"] == "ft" and result["after"] == "RI"


def test_unsupported_caret_method_uses_collapsed_selection(provider):
    from core.client.caret_worker import read_context

    provider.pattern_two.GetCaretRange.side_effect = ProviderError(-2147467263)
    result = read_context(42, 20, 10)
    assert result == {"before": "left", "after": "RIGHT",
                      "status": "captured", "method": "text_pattern"}


def test_null_caret_range_does_not_fall_back(provider):
    from core.client.caret_worker import read_context

    provider.pattern_two.GetCaretRange.return_value = (True, None)
    assert read_context(42, 20, 10)["status"] == "provider_error"
    assert not provider.reads


def test_worker_clamps_large_and_negative_limits(provider):
    from core.client.caret_worker import read_context

    provider.text = "l" * 2500 + "r" * 1500
    provider.pattern.DocumentRange = provider.Range(0, len(provider.text))
    provider.caret.points = provider.selection.points = [2500, 2500]
    result = read_context(42, 100000, 100000)
    assert result["before"] == "l" * 2000 and result["after"] == "r" * 1000
    result = read_context(42, -1, 2)
    assert result["before"] == "" and result["after"] == "rr"


def test_worker_never_reads_without_an_expected_foreground(provider, monkeypatch):
    from core.client.caret_worker import read_context

    monkeypatch.setattr("core.client.caret_context.foreground_window", lambda: 0)
    assert read_context(0, 20, 10) == {"status": "focus_changed", "method": "none"}
    provider.comtypes.CoInitialize.assert_not_called()
    assert not provider.reads


@pytest.mark.parametrize("pattern2", [True, None])
@pytest.mark.parametrize("change", ["element", "window", "selection", "caret"])
def test_worker_discards_snapshot_after_focus_or_selection_change(provider, monkeypatch, pattern2, change):
    from core.client.caret_worker import read_context

    provider.pattern2 = pattern2
    if change == "element":
        provider.automation.CompareElements.return_value = False
    elif change == "window":
        monkeypatch.setattr("core.client.caret_context.foreground_window", Mock(side_effect=[42, 43]))
    else:
        new_range = provider.Range(4, 6) if change == "selection" else provider.Range(6, 6)
        provider.pattern.GetSelection.side_effect = [
            provider.ranges, SimpleNamespace(Length=1, GetElement=lambda _: new_range)]
    result = read_context(42, 20, 10)
    assert result["status"] in {"focus_changed", "selection", "caret_mismatch"}
    assert "before" not in result and "after" not in result


@pytest.mark.parametrize("reason", [
    "range_not_collapsed", "caret_selection_disagree", "caret_before_control",
    "caret_after_control", "selection_changed",
])
def test_mismatch_reason_identifies_boundary_without_returning_text(provider, reason):
    from core.client.caret_worker import read_context

    if reason == "range_not_collapsed":
        provider.caret.points = [3, 4]
    elif reason == "caret_selection_disagree":
        provider.caret.points = [3, 3]
    elif reason == "caret_before_control":
        provider.pattern.DocumentRange = provider.Range(5, 9)
    elif reason == "caret_after_control":
        provider.pattern.DocumentRange = provider.Range(0, 3)
    else:
        new_range = provider.Range(6, 6)
        provider.pattern.GetSelection.side_effect = [
            provider.ranges, SimpleNamespace(Length=1, GetElement=lambda _: new_range)]
    assert read_context(42, 20, 10) == {
        "status": "caret_mismatch", "method": "text_pattern2", "reason": reason,
    }
    if reason != "selection_changed":
        assert not provider.reads


def test_empty_editor_and_zero_length_bounds(provider):
    from core.client.caret_worker import read_context

    provider.text = ""
    provider.selection.points = provider.caret.points = [0, 0]
    assert read_context(42, 20, 10)["status"] == "empty"
    provider.reads.clear()
    assert read_context(42, 0, 0)["status"] == "empty"
    assert not provider.reads


@pytest.mark.parametrize("missing", [None, -2147220988])
def test_unsupported_text_pattern_is_content_free(provider, missing):
    from core.client.caret_worker import read_context

    if missing is None:
        provider.element.GetCurrentPattern.side_effect = None
        provider.element.GetCurrentPattern.return_value = None
    else:
        provider.element.GetCurrentPattern.side_effect = ProviderError(missing)
    assert read_context(42, 20, 10) == {"status": "unsupported_text_pattern", "method": "none"}
    assert not provider.reads


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
        json.dumps({"before": "private", "after": "text",
                    "status": "captured", "method": "text_pattern"}).encode(),
        b"",
    )
    monkeypatch.setattr("core.client.caret_context.subprocess.Popen", lambda *a, **k: process)
    monkeypatch.setattr("core.client.caret_context.foreground_window", Mock(side_effect=[42, 43]))
    capture = CaretContextCapture(SimpleNamespace(caret_context_enabled=True), Path("."))
    assert asyncio.run(capture.capture(42)) == ""


def test_snapshot_is_bounded_and_zero_before_means_no_prefix(monkeypatch):
    process = Mock(returncode=0)
    process.communicate.return_value = (
        b'{"before":"abc","after":"xyz","status":"captured","method":"text_pattern"}', b"")
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


@pytest.mark.parametrize("response,status", [
    ({"status": "captured", "method": "text_pattern", "before": "private", "after": "text"}, "captured"),
    ({"status": "empty", "method": "text_pattern2", "before": "", "after": ""}, "empty"),
    ({"status": "password", "method": "none", "before": "private"}, "password"),
    ({"status": "private", "method": "none"}, "invalid_response"),
    ({"status": "empty", "method": "private"}, "invalid_response"),
    ({"status": [], "method": "none"}, "invalid_response"),
    ({"status": "captured", "method": "text_pattern", "before": 12}, "invalid_response"),
    ({"status": "empty", "method": "text_pattern", "before": "private"}, "invalid_response"),
    ({"status": "captured", "method": "none", "before": "private"}, "invalid_response"),
    ({"status": "caret_mismatch", "method": "text_pattern", "reason": "caret_after_control"}, "caret_mismatch"),
    ({"status": "caret_mismatch", "method": "text_pattern", "reason": "private"}, "invalid_response"),
    ({"status": "caret_mismatch", "method": "text_pattern", "reason": []}, "invalid_response"),
    ({"status": "captured", "method": "text_pattern", "reason": "selection_changed", "before": "private"}, "invalid_response"),
    ({}, "invalid_response"), ([], "invalid_response"),
])
def test_capture_diagnostics_use_only_validated_metadata(monkeypatch, response, status):
    process = Mock(returncode=0)
    process.communicate.return_value = (json.dumps(response).encode(), b"")
    monkeypatch.setattr("core.client.caret_context.subprocess.Popen", lambda *a, **k: process)
    monkeypatch.setattr("core.client.caret_context.foreground_window", lambda: 42)
    log = Mock()
    monkeypatch.setattr("core.client.logger.info", log)
    capture = CaretContextCapture(SimpleNamespace(caret_context_enabled=True), Path("."))
    result = asyncio.run(capture.capture(42, task_id="12345678-abcd"))
    message, *args = log.call_args.args
    record = message % tuple(args)
    assert "task=12345678" in record and f"status={status}" in record
    assert "private" not in record and "Insertion point" not in record
    assert ("reason=caret_after_control" if status == "caret_mismatch" else "reason=none") in record
    if status == "captured":
        assert result == "private\n[Insertion point]\ntext"
        assert "before_chars=7 after_chars=4" in record
    else:
        assert not result and "before_chars=0 after_chars=0" in record


@pytest.mark.parametrize("failure,status", [
    ("timeout", "timeout"), ("exit", "helper_failed"), ("json", "invalid_response"),
    ("spawn", "helper_error"), ("closed", "closed"), ("busy", "busy"),
])
def test_capture_failure_is_observable_and_releases_lock(monkeypatch, failure, status):
    process = Mock(returncode=1 if failure == "exit" else 0)
    process.communicate.return_value = (b"private invalid JSON", b"")
    if failure == "timeout":
        process.communicate.side_effect = [subprocess.TimeoutExpired("private", 1.5), (b"", b"")]
    spawn = Mock(return_value=process)
    if failure == "spawn":
        spawn.side_effect = OSError("private")
    monkeypatch.setattr("core.client.caret_context.subprocess.Popen", spawn)
    monkeypatch.setattr("core.client.caret_context.foreground_window", lambda: 42)
    log = Mock()
    monkeypatch.setattr("core.client.logger.info", log)
    capture = CaretContextCapture(SimpleNamespace(caret_context_enabled=True), Path("."))
    if failure == "closed":
        capture.close()
    if failure == "busy":
        capture._lock.acquire()
    assert asyncio.run(capture.capture(42, task_id="private task")) == ""
    if failure == "busy":
        capture._lock.release()
    assert not capture._lock.locked()
    message, *args = log.call_args.args
    record = message % tuple(args)
    assert f"status={status}" in record and "task=-" in record and "private" not in record
    if failure == "timeout":
        process.kill.assert_called_once()
    if failure in {"busy", "closed"}:
        spawn.assert_not_called()


def test_helper_main_emits_only_json_metadata_on_failure(monkeypatch):
    import io
    from core.client import caret_worker

    output = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(caret_worker, "read_context", Mock(side_effect=RuntimeError("private")))
    assert caret_worker.main(["42", "800", "200"]) == 0
    assert json.loads(output.getvalue()) == {"status": "provider_error", "method": "none"}


def test_close_during_capture_discards_output_and_prevents_restart(monkeypatch):
    capture = CaretContextCapture(SimpleNamespace(caret_context_enabled=True), Path("."))
    process = Mock(returncode=0)

    def communicate(**kwargs):
        capture.close()
        return b'{"status":"captured","method":"text_pattern","before":"private"}', b""

    process.communicate.side_effect = communicate
    spawn = Mock(return_value=process)
    monkeypatch.setattr("core.client.caret_context.subprocess.Popen", spawn)
    monkeypatch.setattr("core.client.caret_context.foreground_window", lambda: 42)
    assert asyncio.run(capture.capture(42)) == ""
    assert asyncio.run(capture.capture(42)) == ""
    process.kill.assert_called_once()
    spawn.assert_called_once()
    assert capture._process is None
