"""Synthetic content must survive product output without entering diagnostics."""

import ast
import asyncio
import io
import json
import logging
from pathlib import Path
import queue
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest
from rich.console import Console
from rich.logging import RichHandler

from core import get_logger
from core.log_archive import DiagnosticArchiveHandler
from core.logger import TruncatingFileHandler


TRANSCRIPT = "SYNTHETIC_PRIVATE_TRANSCRIPT"
PROMPT = "SYNTHETIC_PRIVATE_PROMPT"
CONTEXT = "SYNTHETIC_PRIVATE_CONTEXT"
KEY = "SYNTHETIC_PRIVATE_KEY"
OUTPUT = "SYNTHETIC_PRIVATE_OUTPUT"
SECRETS = (TRANSCRIPT, PROMPT, CONTEXT, KEY, OUTPUT)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def diagnostics(tmp_path, monkeypatch):
    """Exercise the actual four sink types, isolated from the user's log files."""
    from core.client.manager.file_runner import TranscriptionTaskLog

    log_root = tmp_path / "logs"
    log_root.mkdir()
    console_text = io.StringIO()
    handlers = []
    for name in ("client", "server"):
        logger = get_logger(name)
        sinks = [
            TruncatingFileHandler(log_root / f"{name}_latest.log", encoding="utf-8"),
            DiagnosticArchiveHandler(log_root, name, retention_days=0),
            RichHandler(console=Console(file=console_text, width=200), rich_tracebacks=True),
        ]
        monkeypatch.setattr(logger, "handlers", sinks)
        monkeypatch.setattr(logger, "level", logging.DEBUG)
        monkeypatch.setattr(logger, "_cache", {})
        handlers.extend(sinks)
        logger.debug("Diagnostic sink initialized")
    run_log = TranscriptionTaskLog(tmp_path)
    run_log.start()
    try:
        yield get_logger("server")
    finally:
        run_log.close()
        for handler in handlers:
            handler.flush()
            handler.close()
        texts = [console_text.getvalue()]
        texts.extend(path.read_text(encoding="utf-8") for path in log_root.rglob("*.log"))
        assert len(texts) >= 6  # console, two latest, two archives, one run log
        for text in texts:
            for secret in SECRETS:
                assert secret not in text


@pytest.mark.parametrize("source", ["mic", "file"])
def test_recognition_and_formatting_preserve_results_without_echoes(
    diagnostics, monkeypatch, capsys, source
):
    from core.server.worker.pipeline import TaskPipeline
    from core.server.engines.base import EngineCapabilities
    from core.server.schema import Task

    monkeypatch.setattr("core.server.formatter.text_formatter.Config.format_num", True)
    monkeypatch.setattr("core.server.formatter.text_formatter.Config.format_spell", False)
    monkeypatch.setattr("core.server.formatter.text_formatter.chinese_to_num",
                        Mock(side_effect=ValueError(TRANSCRIPT)))
    stream = SimpleNamespace(accept_waveform=Mock(), result=SimpleNamespace(
        text=TRANSCRIPT, tokens=[TRANSCRIPT], timestamps=[0.0],
    ))
    recognizer = SimpleNamespace(create_stream=lambda: stream, decode_stream=Mock(),
                                 capabilities=[EngineCapabilities.TIMESTAMPS])
    pipeline = TaskPipeline(recognizer, punc_model=SimpleNamespace(
        punctuate=Mock(side_effect=RuntimeError(TRANSCRIPT))))
    task = Task(source, np.zeros(3200, dtype=np.float32).tobytes(), 0, 0,
                "task", "socket", True, 0, 0, context=CONTEXT)
    result = pipeline.process(task)
    assert result.text == result.text_accu == TRANSCRIPT
    assert result.is_final
    recognizer.decode_stream.assert_called_once_with(stream, context=CONTEXT, language="auto")
    assert TRANSCRIPT not in capsys.readouterr().out

    recognizer.decode_stream.side_effect = RuntimeError(PROMPT)
    with pytest.raises(RuntimeError, match=PROMPT):
        pipeline.process(task)


def _load_function(relative_path, name, namespace, class_name=None):
    """Execute the maintained function without importing native DLL/model bindings."""
    from core.i18n import Notice, tr
    namespace.update(Notice=Notice, tr=tr)
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    body = tree.body
    if class_name:
        body = next(node.body for node in body if isinstance(node, ast.ClassDef)
                    and node.name == class_name)
    function = next(node for node in body if isinstance(node, ast.FunctionDef) and node.name == name)
    function.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("backend", ["llama", "fun_asr_gguf", "qwen_asr_gguf", "force_aligner_gguf"])
def test_native_callbacks_preserve_diagnostic_text_and_severity(diagnostics, backend):
    relative = f"core/server/engines/{backend}/"
    relative += "llama.py" if backend == "llama" else "inference/llama.py"
    captured = Mock(wraps=diagnostics)
    callback = _load_function(relative, "logger_callback", {"logger": captured, "logging": logging})
    # ggml/include/ggml.h: DEBUG=1, INFO=2, WARN=3, ERROR=4, CONT=5.
    for native_level, expected in [(1, logging.DEBUG), (2, logging.INFO),
                                    (3, logging.WARNING), (4, logging.ERROR),
                                    (0, logging.DEBUG), (5, logging.DEBUG),
                                    (99, logging.DEBUG)]:
        message = b"native context allocation failed: requested=1024 MiB [buffer] 95%\n"
        callback(native_level, message, None)
        args = captured.log.call_args.args
        assert args[0] == expected
        record = diagnostics.makeRecord(diagnostics.name, args[0], "", 0, args[1], args[2:], None)
        assert record.getMessage() == message.decode().strip()
        assert captured.log.call_args.kwargs["extra"]["markup"] is False
    callback(3, b"backend name: \xff\n", None)
    assert captured.log.call_args.args[2] == "backend name: \ufffd"
    before = captured.log.call_count
    for blank in (None, b"", b".\n", b" \t\n"):
        callback(4, blank, None)
    assert captured.log.call_count == before


@pytest.mark.parametrize("backend", ["llama", "fun_asr_gguf", "qwen_asr_gguf", "force_aligner_gguf"])
def test_native_startup_info_does_not_flood_warning_console(backend):
    relative = f"core/server/engines/{backend}/"
    relative += "llama.py" if backend == "llama" else "inference/llama.py"
    output = io.StringIO()
    handler = RichHandler(level=logging.WARNING, console=Console(file=output, width=200),
                          show_time=False, show_path=False, markup=True)
    logger = logging.Logger("native-startup-test", level=logging.DEBUG)
    logger.addHandler(handler)
    callback = _load_function(relative, "logger_callback", {"logger": logger, "logging": logging})
    try:
        for _ in range(120):
            callback(2, b"synthetic model loading progress", None)
        callback(1, b"synthetic debug", None)
        callback(5, b"synthetic continuation", None)
        assert output.getvalue() == ""
        warning = "synthetic backend warning: [buffer] 95%"
        error = "synthetic allocation failure: requested=1024 MiB"
        callback(3, warning.encode(), None)
        callback(4, error.encode(), None)
        text = output.getvalue()
        assert text.count("WARNING") == text.count("ERROR") == 1
        assert warning in text and error in text
        assert "Native backend diagnostic:" not in text
    finally:
        handler.close()


@pytest.mark.parametrize("backend", ["qwen_asr_gguf", "force_aligner_gguf"])
def test_alignment_fallback_keeps_token_out_of_warning(diagnostics, backend):
    reconcile = _load_function(
        f"core/server/engines/{backend}/inference/aligner.py", "reconcile",
        {"logger": diagnostics, "ForcedAlignItem": SimpleNamespace}, "AlignerProcessor",
    )
    item = SimpleNamespace(text=TRANSCRIPT, start_time=0.0, end_time=1.0)
    processor = SimpleNamespace(_find_token_indices=lambda *args: (-1, -1))
    result = reconcile(processor, OUTPUT, [item])
    assert result[0] is item and result[-1].text == OUTPUT


@pytest.mark.parametrize("fail_at", ["construct", "run"])
def test_asr_process_failure_has_nonzero_exit_without_content_traceback(
    diagnostics, monkeypatch, fail_at
):
    from core.server.worker import start_worker

    worker = Mock()
    constructor = Mock(return_value=worker)
    (constructor if fail_at == "construct" else worker.run).side_effect = RuntimeError(PROMPT)
    monkeypatch.setattr("core.server.worker.RecognizerWorker", constructor)
    failure = Mock()
    with pytest.raises(SystemExit) as caught:
        start_worker(None, None, None, None, None, 0, failure)
    assert caught.value.code == 1
    failure.set.assert_called_once()
    assert PROMPT not in "".join(traceback.format_exception(caught.value))


def test_aligner_errors_do_not_echo_request_or_remote_error(diagnostics, monkeypatch):
    from core.server.worker.aligner_worker import start_aligner_worker
    from core.server.engines.manager import ProcessAlignerProxy
    from core.server.schema import AlignRequest, AlignResponse

    engine = SimpleNamespace(align=Mock(side_effect=ValueError(TRANSCRIPT)),
                             cleanup=Mock(side_effect=RuntimeError(PROMPT)))
    monkeypatch.setattr("core.server.engines.factory.EngineFactory.create_align_engine", lambda: engine)
    requests, responses = queue.Queue(), queue.Queue()
    requests.put(AlignRequest("request", "task", np.zeros(1), TRANSCRIPT))
    requests.put(None)
    start_aligner_worker(requests, responses)
    response = responses.get_nowait()
    assert response.error == "ValueError" and response.result is None
    # An older/foreign worker may still supply arbitrary response.error text.
    outgoing = Mock()
    incoming = SimpleNamespace(get=lambda **kwargs: AlignResponse(
        outgoing.put.call_args.args[0].request_id, "task", error=TRANSCRIPT))
    assert ProcessAlignerProxy(outgoing, incoming).align([], TRANSCRIPT, task_id="task") is None


def test_aligner_startup_error_has_content_free_exit(diagnostics, monkeypatch):
    from core.server.worker.aligner_worker import start_aligner_worker
    from core.server.schema import AlignRequest

    monkeypatch.setattr("core.server.engines.factory.EngineFactory.create_align_engine",
                        Mock(side_effect=RuntimeError(PROMPT)))
    requests = queue.Queue()
    requests.put(AlignRequest("request", "task", [], TRANSCRIPT))
    with pytest.raises(SystemExit) as caught:
        start_aligner_worker(requests, queue.Queue())
    assert caught.value.code == 1
    assert PROMPT not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("failure", [None, "api", "exception"])
def test_llm_payload_and_response_do_not_enter_diagnostics(
    diagnostics, monkeypatch, tmp_path, failure
):
    from core.client.llm.config import Catalog, Preset, Provider
    from core.client.llm.errors import api_error
    from core.client.llm.service import TextActionService

    provider = Provider("provider", "openai", "https://synthetic.invalid", "model", api_key=KEY)
    preset = Preset("correct_asr", "Correction", provider.id, PROMPT, use_caret_context=True)
    monkeypatch.setattr("core.client.llm.service.load_catalog",
                        lambda path: Catalog({provider.id: provider}, {preset.id: preset}))
    transport = SimpleNamespace(complete=AsyncMock(return_value=OUTPUT))
    if failure == "api":
        transport.complete.side_effect = api_error(400, {"error": {
            "message": TRANSCRIPT + PROMPT + KEY, "code": CONTEXT,
            "details": [{"reason": OUTPUT, "retryDelay": KEY}],
        }}, retry_after=KEY)
    elif failure:
        transport.complete.side_effect = RuntimeError(TRANSCRIPT + PROMPT + KEY)
    service = TextActionService(SimpleNamespace(llm_enabled=True), tmp_path, transport=transport)
    result = asyncio.run(service.process(TRANSCRIPT, context=CONTEXT))
    assert result.text == (TRANSCRIPT if failure else OUTPUT)
    assert result.processed == (failure is None)
    args = transport.complete.call_args.args
    assert args[1][0]["content"] == PROMPT
    assert json.loads(args[1][1]["content"]) == {
        "transcript": TRANSCRIPT, "surrounding_text_reference": CONTEXT,
    }
    assert not any(secret in result.error_message for secret in SECRETS)


@pytest.mark.parametrize("transcripts,llm_records", [(False, False), (True, False),
                                                       (False, True), (True, True)])
def test_content_record_switches_and_product_output_remain_independent(
    diagnostics, monkeypatch, tmp_path, transcripts, llm_records
):
    from core.client.output import result_processor as module
    from core.client.llm.service import TextResult
    from core.client.diary.diary_writer import DiaryWriter
    from core.client.state import ClientState

    for name, value in dict(save_transcripts=transcripts, save_llm_records=llm_records,
                            save_audio=False, transcript_save_original=True,
                            traditional_convert=False, enter_apps=[]).items():
        monkeypatch.setattr(module.Config, name, value, raising=False)
    monkeypatch.setattr(module, "get_active_window_info", lambda: {})
    monkeypatch.setattr(module, "broadcast_output_udp", Mock())
    monkeypatch.setattr("core.ui.show_status_hint", Mock())
    preview = io.StringIO()
    monkeypatch.setattr(module, "console", Console(file=preview, width=200))
    state = ClientState(task_contexts={"task": (CONTEXT, 0)})
    state.set_output_text = Mock()
    app = SimpleNamespace(
        state=state, progress=Mock(), output=SimpleNamespace(output=AsyncMock()),
        llm=SimpleNamespace(process=AsyncMock(return_value=TextResult(
            OUTPUT, TRANSCRIPT, processed=True))),
        diary=DiaryWriter(tmp_path / "transcripts"),
        action_records=DiaryWriter(tmp_path / "actions"),
    )
    async def run():
        processor = module.ResultProcessor(app)
        await processor._handle_final(SimpleNamespace(task_id="task", text=TRANSCRIPT, time_start=0))

    asyncio.run(run())
    assert app.output.output.call_args.args[0] == OUTPUT
    assert TRANSCRIPT in preview.getvalue() and OUTPUT in preview.getvalue()
    for folder, enabled in [("transcripts", transcripts), ("actions", llm_records)]:
        paths = list((tmp_path / folder).rglob("*.md"))
        assert bool(paths) == enabled
        if paths:
            content = paths[0].read_text(encoding="utf-8")
            assert OUTPUT in content and TRANSCRIPT in content


def test_saved_audio_names_and_file_outputs_do_not_enter_diagnostics(diagnostics, monkeypatch, tmp_path):
    from core.client.audio.file_manager import AudioFileManager
    from core.client.state import ClientState
    from core.client.transcribe.result_handler import ResultHandler

    monkeypatch.setattr("core.client.audio.file_manager.Config.audio_name_len", 200)
    manager = AudioFileManager.__new__(AudioFileManager)
    manager.file_path = tmp_path / "recording.wav"
    manager.file_path.write_bytes(b"synthetic placeholder")
    path = manager.rename(TRANSCRIPT, 0)
    assert TRANSCRIPT in path.name and path.is_file()
    state = ClientState()
    state.register_audio_file("task", path)
    assert state.pop_audio_file("task") == path
    message = SimpleNamespace(text=TRANSCRIPT, text_accu=TRANSCRIPT,
                              tokens=[TRANSCRIPT], timestamps=[0.0])
    display, _, paths = ResultHandler.save_results(path, message, output_formats={"merge", "txt", "json"})
    assert display == TRANSCRIPT
    assert all(TRANSCRIPT in output.read_text(encoding="utf-8") for output in paths)
    monkeypatch.setattr(Path, "rename", Mock(side_effect=OSError(TRANSCRIPT)))
    assert manager.rename(TRANSCRIPT, 0) == path


@pytest.mark.parametrize("operation", ["send", "receive"])
@pytest.mark.parametrize("closed", [True, False])
def test_transport_errors_do_not_echo_close_reason_or_exception(diagnostics, operation, closed):
    from core.client.connection.websocket_manager import CommunicationError, WebSocketManager
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    error = ConnectionClosedError(Close(1011, TRANSCRIPT), None) if closed else ValueError(TRANSCRIPT)
    connection = SimpleNamespace(send=AsyncMock(side_effect=error), recv=AsyncMock(side_effect=error))
    state = SimpleNamespace(websocket=connection, is_connected=True)
    manager = WebSocketManager(SimpleNamespace(state=state))
    request = manager.send(SimpleNamespace(to_json=lambda: PROMPT)) if operation == "send" else manager.receive()
    with pytest.raises(CommunicationError) as caught:
        asyncio.run(request)
    assert TRANSCRIPT not in "".join(traceback.format_exception(caught.value))
    get_logger("client").warning("Transport failure: %s", caught.value)


def test_clipboard_failures_do_not_echo_content(diagnostics, monkeypatch):
    from core.client.clipboard.clipboard import safe_copy, safe_paste

    monkeypatch.setattr("pyclip.copy", Mock(side_effect=RuntimeError(TRANSCRIPT)))
    monkeypatch.setattr("pyclip.paste", Mock(side_effect=RuntimeError(CONTEXT)))
    assert safe_copy(TRANSCRIPT) is False
    assert safe_paste() == ""


@pytest.mark.parametrize("failure", [False, True])
def test_file_batch_logs_metadata_and_preserves_result_paths(
    diagnostics, monkeypatch, tmp_path, failure
):
    from core.client.manager.file_runner import FileRunner, resolve_input_paths
    from core.client.transcribe.file_transcriber import FileTranscriber

    source_dir = tmp_path / TRANSCRIPT
    source_dir.mkdir()
    source = source_dir / f"{TRANSCRIPT}.wav"
    source.touch()
    assert resolve_input_paths([source_dir]) == [source]
    monkeypatch.setattr("core.client.ui.TipsDisplay.show_file_tips", Mock())
    monkeypatch.setattr("core.client.manager.file_runner.console", Mock())
    monkeypatch.setattr("core.client.manager.file_runner.sys.stdin", SimpleNamespace(isatty=lambda: False))
    app = SimpleNamespace(base_dir=tmp_path)
    runner = FileRunner(app, [source], output_formats=frozenset({"txt", "json"}))

    async def process(path):
        if failure:
            raise RuntimeError(TRANSCRIPT + PROMPT)
        transcriber = FileTranscriber(app, path, output_formats=frozenset({"txt", "json"}))
        message = SimpleNamespace(
            text=TRANSCRIPT, text_accu=TRANSCRIPT, tokens=[TRANSCRIPT], timestamps=[0.0],
            duration=1, time_start=0, time_complete=1, task_id=transcriber.task_id,
            error_code="", is_final=True)
        app.ws = SimpleNamespace(receive=AsyncMock(return_value=message))
        transcriber._send_complete.set()
        assert await transcriber.receive()
        return transcriber.summary

    monkeypatch.setattr(runner, "_process_file", process)
    assert asyncio.run(runner.run()) is (not failure)
    if not failure:
        assert TRANSCRIPT in source.with_suffix(".txt").read_text(encoding="utf-8")


def test_file_batch_unexpected_failure_returns_failure_without_traceback(diagnostics, monkeypatch, tmp_path):
    from core.client.manager.file_runner import FileRunner

    monkeypatch.setattr("core.client.ui.TipsDisplay.show_file_tips", Mock())
    monkeypatch.setattr("core.client.manager.file_runner.console", SimpleNamespace(
        print=Mock(side_effect=RuntimeError(TRANSCRIPT))))
    runner = FileRunner(SimpleNamespace(base_dir=tmp_path), [Path("test.wav")],
                        output_formats=frozenset({"txt"}))
    assert asyncio.run(runner.run()) is False


def test_subtitle_rebuild_error_hides_content_but_keeps_failure(diagnostics, monkeypatch, tmp_path):
    from core.client.transcribe.srt_adjuster import SrtAdjuster

    preview = Mock()
    monkeypatch.setattr("core.client.transcribe.srt_adjuster.console", preview)
    monkeypatch.setattr(SrtAdjuster, "_load_words", Mock(side_effect=ValueError(TRANSCRIPT)))
    assert SrtAdjuster().adjust(tmp_path / "text.txt", tmp_path / "tokens.json") is False
    assert TRANSCRIPT not in str(preview.mock_calls)


def test_model_initialization_error_does_not_print_prompt(diagnostics, capsys):
    initialize = _load_function("core/server/engines/fun_asr_gguf/inference/models.py", "__init__", {
        "timer": Mock(side_effect=RuntimeError(PROMPT)),
        "vprint": lambda message, verbose: diagnostics.error(message),
    }, "Models")
    with pytest.raises(RuntimeError) as caught:
        initialize(SimpleNamespace(_load_models=Mock()), SimpleNamespace(verbose=True))
    assert PROMPT not in "".join(traceback.format_exception(caught.value))
    assert PROMPT not in capsys.readouterr().err
