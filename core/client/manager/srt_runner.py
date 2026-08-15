# coding: utf-8
"""已有 TXT/JSON 结果的 SRT 重建运行器。"""

from __future__ import annotations

from pathlib import Path


class SrtRebuildRunner:
    def __init__(self, text_file: Path | None, json_file: Path | None):
        if text_file is None or json_file is None:
            raise ValueError("字幕重建缺少 TXT 或 JSON")
        self.text_file = text_file
        self.json_file = json_file

    async def run(self) -> bool:
        from ..transcribe import SrtAdjuster

        return SrtAdjuster().adjust(self.text_file, self.json_file)
