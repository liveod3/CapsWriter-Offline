# coding: utf-8
"""
SRT 调整模块

提供 SrtAdjuster 类用于调整 SRT 字幕时间轴。
"""

from __future__ import annotations

from core.i18n import Notice, tr

import json
import math
import uuid
from pathlib import Path

from core.client.state import console
from core.tools import srt_from_txt
from . import logger



class SrtAdjuster:
    """
    SRT 字幕调整器
    
    根据文本文件重新生成 SRT 字幕时间轴。
    """
    
    @staticmethod
    def _load_words(json_file: Path) -> list[dict]:
        """读取并严格校验字幕重建需要的 token 时间戳。"""
        with open(json_file, 'r', encoding='utf-8') as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError(Notice('validation.srt_adjuster.json_top_level_must_be_an_object'))

        tokens = payload.get('tokens')
        timestamps = payload.get('timestamps')
        if not isinstance(tokens, list) or not isinstance(timestamps, list):
            raise ValueError(Notice('validation.srt_adjuster.json_must_contain_tokens_and_timestamps_arrays'))
        if not tokens or len(tokens) != len(timestamps):
            raise ValueError(Notice('validation.srt_adjuster.tokens_and_timestamps_must_be_nonempty_and_have'))

        normalized_timestamps = []
        previous = -1.0
        for index, timestamp in enumerate(timestamps):
            if (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp)
                or timestamp < 0
            ):
                raise ValueError(Notice('validation.srt_adjuster.timestamps_must_be_finite_and_nonnegative', value0=index))
            value = float(timestamp)
            if value < previous:
                raise ValueError(Notice('validation.srt_adjuster.timestamps_must_be_in_nondecreasing_order'))
            normalized_timestamps.append(value)
            previous = value

        for index, token in enumerate(tokens):
            if not isinstance(token, str):
                raise ValueError(Notice('validation.srt_adjuster.tokens_must_be_a_string', value0=index))

        words = [
            {
                'word': token.replace('@', ''),
                'start': timestamp,
                'end': timestamp + 0.2,
            }
            for token, timestamp in zip(tokens, normalized_timestamps)
        ]
        for index in range(len(words) - 1):
            words[index]['end'] = min(
                words[index]['end'],
                words[index + 1]['start'],
            )
        return words

    @staticmethod
    def _allocate_output(text_file: Path) -> tuple[Path, int]:
        """为重建的 SRT 分配不覆盖已有结果的编号。"""
        sequence = 1
        while True:
            output = (
                text_file.with_suffix('.srt')
                if sequence == 1
                else text_file.with_name(f'{text_file.stem} ({sequence}).srt')
            )
            if not output.exists():
                return output, sequence
            sequence += 1

    def adjust(self, text_file: Path, json_file: Path) -> bool:
        """
        使用显式指定的 TXT 和 JSON 重建 SRT 字幕。
        
        Args:
            text_file: 人工校对后的文本文件
            json_file: 包含 tokens 与 timestamps 的 JSON 文件
        """
        task_id = str(uuid.uuid1())
        console.print(tr('srt.title'))
        console.print(tr('srt.text', value0=text_file))
        console.print(tr('srt.timestamps', value0=json_file))
        
        logger.info(Notice('diagnostic.srt_adjuster.subtitle_rebuild_started'))
        
        try:
            words = self._load_words(json_file)
            with open(text_file, 'r', encoding='utf-8') as stream:
                text_lines = stream.readlines()
            if not any(line.strip() for line in text_lines):
                raise ValueError(Notice('validation.srt_adjuster.txt_content_is_empty'))

            output_file, sequence = self._allocate_output(text_file)
            srt_from_txt.generate_srt_file(words, text_lines, output_file)
            if sequence > 1:
                console.print(
                    tr('srt.numbered_output', value0=sequence)
                )
            console.print(tr('srt.complete', value0=output_file))
            logger.info(Notice('diagnostic.srt_adjuster.subtitle_rebuild_completed'))
            return True
        except Exception as e:
            console.print(tr('srt.failed', value0=type(e).__name__))
            logger.error(Notice('diagnostic.srt_adjuster.subtitle_rebuild_failed_error'), type(e).__name__)
            return False
