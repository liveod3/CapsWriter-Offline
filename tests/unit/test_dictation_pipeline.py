import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from core.client.audio.recorder import AudioRecorder
from core.client.output.result_processor import ResultProcessor
from core.client.llm.service import TextResult
from core.client.state import ClientState


def test_caret_snapshot_is_once_per_recording_and_audio_is_fifo(monkeypatch):
    async def run():
        monkeypatch.setattr("core.client.audio.recorder.Config.save_audio", False)
        monkeypatch.setattr("core.client.audio.recorder.Config.threshold", 0.3)
        queue = asyncio.Queue()
        chunks = [np.full((6, 1), x, dtype=np.float32) for x in (1, 2, 3)]
        for event in [
            {"type": "begin", "time": 10, "target_window": 42},
            *[
                {"type": "data", "time": t, "data": chunk}
                for t, chunk in zip((10.1, 10.4, 10.5), chunks)
            ],
            {"type": "finish"},
        ]:
            queue.put_nowait(event)
        state = ClientState(queue_in=queue)
        app = SimpleNamespace(
            progress=Mock(),
            state=state,
            caret_context=SimpleNamespace(capture=AsyncMock(return_value="surrounding")),
            ws=SimpleNamespace(is_connected=True, send=AsyncMock(return_value=True)),
        )
        recorder = AudioRecorder(app)
        await recorder.record_and_send()
        app.caret_context.capture.assert_awaited_once_with(42, task_id=recorder.task_id)
        messages = [call.args[0] for call in app.ws.send.call_args_list]
        assert [m.is_final for m in messages] == [False, False, True]
        assert all(m.supports_task_errors for m in messages)
        assert len({m.context for m in messages}) == 1
        decoded = np.concatenate(
            [np.frombuffer(base64.b64decode(m.data), dtype=np.float32) for m in messages if m.data]
        )
        np.testing.assert_array_equal(decoded, [1, 1, 2, 2, 3, 3])
        assert state.task_contexts[messages[0].task_id] == ("surrounding", 42)

    asyncio.run(run())


@pytest.mark.parametrize("focus_changed,cancelled", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("missing_key", [False, True])
def test_final_output_preserves_plain_asr_and_respects_cancel_and_focus(
    monkeypatch, focus_changed, cancelled, missing_key
):
    async def run():
        monkeypatch.setattr("core.client.output.result_processor.Config.traditional_convert", False)
        monkeypatch.setattr("core.client.output.result_processor.Config.enter_apps", [])
        monkeypatch.setattr(
            "core.client.output.result_processor.foreground_window",
            lambda: 43 if focus_changed else 42,
        )
        monkeypatch.setattr(
            "core.client.output.result_processor.get_active_window_info", lambda: {}
        )
        monkeypatch.setattr("core.client.output.result_processor.broadcast_output_udp", Mock())
        hint = Mock()
        monkeypatch.setattr("core.ui.show_status_hint", hint)
        error_message = "API key missing. Fill api_key in providers.toml."
        state = ClientState(task_contexts={"id": ("context", 42)})
        state.set_output_text = Mock()
        state.dictation_uploads['id'] = asyncio.Event()
        state.dictation_uploads['id'].set()
        state.dictation_deadlines['id'] = float('inf')
        app = SimpleNamespace(
            progress=Mock(),
            state=state,
            llm=SimpleNamespace(
                process=AsyncMock(
                    return_value=TextResult(
                        "苦的",
                        "苦的",
                        cancelled=cancelled,
                        error="MissingAPIKeyError" if missing_key else "",
                        error_message=error_message if missing_key else "",
                    )
                )
            ),
            output=SimpleNamespace(output=AsyncMock()),
        )
        processor = ResultProcessor(app)
        processor._save = Mock()
        await processor._handle_message(SimpleNamespace(task_id="id", text="苦的", is_final=True))
        assert state.last_recognition_text == "苦的"
        state.set_output_text.assert_called_once_with("苦的")
        assert app.output.output.await_count == (0 if focus_changed or cancelled else 1)
        if missing_key:
            hint.assert_any_call(
                error_message + " Original transcription retained.", duration_ms=5000
            )
        assert not state.task_contexts
        processor._save.assert_called_once()

    asyncio.run(run())



@pytest.mark.parametrize('mode, llm_text, expected', [
    ('success', '软件。', '軟體'),
    ('translation', 'Hello.', 'Hello'),
    ('disabled', '软件测试。', '軟體測試。'),
    ('failure', '软件测试。', '軟體測試。'),
])
def test_final_formatting_follows_llm_and_preserves_all_recording_stages(monkeypatch, mode, llm_text, expected):
    async def run():
        from core.client.output.result_processor import Config
        original = '软件测试。'
        for key, value in dict(traditional_convert=True, traditional_locale='zh-tw',
                               trash_punc='。.', trash_punc_thresh=2, trash_punc_apps=[],
                               enter_apps=[], paste=True, paste_apps=[], save_audio=False,
                               save_transcripts=True, transcript_save_original=True,
                               save_llm_records=True, save_llm_context=False).items():
            monkeypatch.setattr(Config, key, value)
        monkeypatch.setattr('core.client.output.result_processor.foreground_window', lambda: 42)
        monkeypatch.setattr('core.client.output.result_processor.get_active_window_info', lambda: {})
        udp = Mock()
        monkeypatch.setattr('core.client.output.result_processor.broadcast_output_udp', udp)
        monkeypatch.setattr('core.ui.show_status_hint', Mock())
        processed = mode in ('success', 'translation')
        result = TextResult(llm_text, original, preset_id='translate' if mode == 'translation' else 'correct_asr',
                            processed=processed, error='TimeoutError' if mode == 'failure' else '')
        state = ClientState(task_contexts={'id': ('', 42)})
        state.set_output_text = Mock()
        app = SimpleNamespace(progress=Mock(), state=state, diary=Mock(),
                              llm=SimpleNamespace(process=AsyncMock(return_value=result)),
                              output=SimpleNamespace(output=AsyncMock()))
        processor = ResultProcessor(app)
        message = SimpleNamespace(task_id='id', text=original, time_start=1)
        await processor._handle_final(message)
        assert app.llm.process.call_args.args[0] == original
        assert result.text == llm_text
        assert state.last_recognition_text == original
        state.set_output_text.assert_called_once_with(expected)
        app.output.output.assert_awaited_once_with(expected, paste=True)
        udp.assert_called_once_with(expected)
        archive = app.diary.write.call_args
        assert archive.args[0] == expected
        assert archive.kwargs['original'] == original
        assert archive.kwargs['action_input'] == (original if processed else None)
        assert archive.kwargs['action_output'] == (llm_text if processed else None)

    asyncio.run(run())


def test_disabled_final_formatting_keeps_llm_output(monkeypatch):
    async def run():
        from core.client.output.result_processor import Config
        monkeypatch.setattr(Config, 'traditional_convert', False)
        monkeypatch.setattr(Config, 'trash_punc', '')
        monkeypatch.setattr(Config, 'enter_apps', [])
        monkeypatch.setattr('core.client.output.result_processor.get_active_window_info', lambda: {})
        monkeypatch.setattr('core.client.output.result_processor.broadcast_output_udp', Mock())
        state = ClientState(task_contexts={'id': ('', 0)})
        state.set_output_text = Mock()
        app = SimpleNamespace(progress=Mock(), state=state,
                              llm=SimpleNamespace(process=AsyncMock(return_value=TextResult('软件。', '原文'))),
                              output=SimpleNamespace(output=AsyncMock()))
        processor = ResultProcessor(app)
        processor._save = Mock()
        await processor._handle_final(SimpleNamespace(task_id='id', text='原文'))
        state.set_output_text.assert_called_once_with('软件。')
        assert app.output.output.call_args.args[0] == '软件。'

    asyncio.run(run())
