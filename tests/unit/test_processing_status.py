import asyncio
from queue import Queue
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.client.processing_status import ProcessingStatus
from core.client.shortcut.task import ShortcutTask
from core.client.state import ClientState
from core.ui.recording_indicator import _RecordingIndicator


def test_old_completion_does_not_hide_new_task_and_late_updates_do_not_reopen():
    render = Mock()
    status = ProcessingStatus(render)
    status.begin("old")
    status.update("old", "Waiting for LLM…")
    status.begin("new")
    status.finish("old")
    assert render.call_args.args == ("Transcribing…",)
    status.finish("new")
    status.update("new", "Waiting for LLM…")
    assert render.call_args.args == ("",)
    status.close()
    status.begin("after-close")
    assert render.call_args.args == ("",)


def test_finishing_latest_task_restores_older_pending_task():
    render = Mock()
    status = ProcessingStatus(render)
    status.begin("old")
    status.update("old", "Waiting for LLM…")
    status.begin("new")
    status.finish("new")
    assert render.call_args.args == ("Waiting for LLM…",)
    status.clear()
    status.update("old", "Preparing LLM…")
    assert render.call_args.args == ("",)


def test_finish_shows_transcribing_before_enqueuing_final_audio(monkeypatch):
    events = []
    app = SimpleNamespace(
        mark_user_activity=Mock(), progress=SimpleNamespace(begin=lambda _: events.append("status")),
        state=ClientState(), loop=Mock(),
    )
    task = ShortcutTask(app, SimpleNamespace(key="ctrl_r", is_toggle_key=lambda: False))
    task._progress_id = "task"
    task.task = Future()
    task.is_recording = True
    task._capture = SimpleNamespace(finish=lambda: events.append('enqueue'))
    app.state.capture = task._capture
    app.state.recording_owner = task
    task._status = Mock()
    monkeypatch.setattr("core.client.shortcut.task.hide_recording_indicator", Mock())
    monkeypatch.setattr("core.client.shortcut.task.set_recording_state", Mock())
    monkeypatch.setattr("core.client.shortcut.task.hide_status_hint", Mock())
    task.finish()
    assert events == ["status", "enqueue"]


def test_indicator_calls_are_queued_until_tk_is_ready(monkeypatch):
    from core.ui import recording_indicator as module

    callbacks = []
    manager = SimpleNamespace(post_ui=callbacks.append, root=None)
    monkeypatch.setattr("core.ui.toast_manager.ToastMessageManager", lambda: manager)
    factory = Mock()
    monkeypatch.setattr(module, "_RecordingIndicator", factory)
    monkeypatch.setattr(module, "_indicator", None)
    module.set_processing_status("Transcribing…")
    factory.assert_not_called()
    root = Mock()
    callbacks.pop()(root)
    factory.assert_called_once_with(root)
    factory.return_value._set_processing_impl.assert_called_once_with("Transcribing…")


def test_temporary_hint_restores_pending_processing_and_finish_keeps_error_hint():
    root = Mock()
    indicator = _RecordingIndicator(root)
    indicator._show_hint_impl = Mock()
    indicator._hint_job = "timer"
    indicator._set_processing_impl("Waiting for LLM…")
    indicator._show_hint_impl.assert_not_called()
    indicator._hide_hint_impl()
    root.after_cancel.assert_called_once_with("timer")
    indicator._show_hint_impl.assert_called_once_with("Waiting for LLM…", 0, "#7DD3FC")
    indicator._hint_job = "error-timer"
    indicator._hint_win = Mock()
    indicator._set_processing_impl("")
    indicator._hint_win.destroy.assert_not_called()


def test_persistent_processing_hint_does_not_schedule_auto_hide(monkeypatch):
    from core.ui import recording_indicator as module

    root = Mock()
    window = Mock()
    window.winfo_reqwidth.return_value = 180
    window.winfo_reqheight.return_value = 40
    monkeypatch.setattr(module.tk, "Toplevel", Mock(return_value=window))
    monkeypatch.setattr(module.tk, "Frame", Mock())
    monkeypatch.setattr(module.tk, "Label", Mock())
    monkeypatch.setattr(module, "_get_active_monitor_workarea", lambda: (0, 0, 1920, 1080))
    indicator = _RecordingIndicator(root)
    indicator._set_processing_impl("Waiting for LLM…")
    root.after.assert_not_called()
    indicator._set_processing_impl("")
    window.destroy.assert_called_once()


def test_ui_queue_drains_on_owner_thread_and_rejects_updates_after_close():
    from core.ui.toast_manager import ToastMessageManager

    manager = object.__new__(ToastMessageManager)
    manager.ui_queue = Queue()
    manager.message_queue = Queue()
    manager._ui_closed = False
    manager.is_running = True
    manager.active_windows = []
    manager.root = Mock()
    callback = Mock()
    manager.post_ui(callback)
    callback.assert_not_called()
    manager._process_queue()
    callback.assert_called_once_with(manager.root)
    manager._on_close()
    manager.post_ui(Mock())
    assert manager.ui_queue.empty()


@pytest.mark.parametrize("failure", [False, True])
def test_final_processing_always_clears_status(monkeypatch, failure):
    from core.client.output.result_processor import ResultProcessor

    async def run():
        progress = ProcessingStatus(Mock())
        progress.begin("id")
        processor = ResultProcessor(SimpleNamespace(progress=progress))

        async def handle(message):
            if failure:
                raise RuntimeError("synthetic failure")

        processor._handle_final = handle
        if failure:
            with pytest.raises(RuntimeError):
                await processor._handle_message(SimpleNamespace(is_final=True, task_id="id"))
        else:
            await processor._handle_message(SimpleNamespace(is_final=True, task_id="id"))
        assert not progress._tasks

    asyncio.run(run())
