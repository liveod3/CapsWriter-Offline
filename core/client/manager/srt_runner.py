# coding: utf-8
"""已有 TXT/JSON 结果的 SRT 重建运行器。"""

from __future__ import annotations

from core.i18n import Notice

from pathlib import Path


class SrtRebuildRunner:
    def __init__(self, text_file: Path | None, json_file: Path | None):
        if text_file is None or json_file is None:
            raise ValueError(Notice('validation.srt_runner.subtitle_rebuild_requires_txt_and_json_inputs'))
        self.text_file = text_file
        self.json_file = json_file

    async def run(self) -> bool:
        from ..transcribe import SrtAdjuster

        return SrtAdjuster().adjust(self.text_file, self.json_file)
