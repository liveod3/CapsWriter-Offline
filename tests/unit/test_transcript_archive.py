import asyncio
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.client.diary.diary_writer import DiaryWriter
from core.client.output.result_processor import ResultProcessor
from core.client.llm.service import TextResult
from core.log_archive import DiagnosticArchiveHandler


def test_transcripts_append_by_month_without_audio(tmp_path):
    writer = DiaryWriter(tmp_path / "transcripts")
    now = datetime(2026, 9, 14, 10, 30).timestamp()
    path = writer.write("第一句", now)
    writer.write("第二句", now + 1)
    assert path == tmp_path / "transcripts/2026/09/14.md"
    assert "第一句" in path.read_text(encoding="utf-8")
    assert "第二句" in path.read_text(encoding="utf-8")
    assert list(tmp_path.rglob("*.mp3")) == []
    assert list(tmp_path.rglob("*.wav")) == []


@pytest.mark.parametrize("text_enabled", [False, True])
@pytest.mark.parametrize("audio_enabled", [False, True])
@pytest.mark.parametrize("action_enabled", [False, True])
def test_storage_switches_are_independent(text_enabled, audio_enabled, action_enabled):
    async def run():
        app = SimpleNamespace(
            state=SimpleNamespace(pop_audio_file=Mock(return_value=None)),
            diary=SimpleNamespace(write=Mock()),
            action_records=SimpleNamespace(write=Mock()),
        )
        processor = ResultProcessor(app)
        with patch("core.client.output.result_processor.Config") as config:
            config.save_audio = audio_enabled
            config.save_transcripts = text_enabled
            config.transcript_save_original = False
            config.save_llm_records = action_enabled
            processor._save(
                SimpleNamespace(task_id="id", time_start=0),
                "原文",
                "结果",
                TextResult("结果", "原文", "correct_asr", processed=True),
            )
        assert app.diary.write.called == (text_enabled or action_enabled)
        assert not app.action_records.write.called
        if text_enabled or action_enabled:
            assert app.diary.write.call_count == 1
            assert (app.diary.write.call_args.kwargs['action_input'] is not None) == action_enabled

    asyncio.run(run())


def test_diagnostic_disabled_does_not_create_files(tmp_path, monkeypatch):
    from config_client import ClientConfig
    from core.logger import Logger

    monkeypatch.setattr(ClientConfig, "save_diagnostic_logs", False, raising=False)
    logger = Logger.setup("test-no-diagnostics", str(tmp_path / "logs"))
    try:
        logger.info("synthetic")
        assert not (tmp_path / "logs").exists()
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        Logger._loggers.pop("test-no-diagnostics", None)
