# coding: utf-8
"""
SRT 调整模块

提供 SrtAdjuster 类用于调整 SRT 字幕时间轴。
"""

from __future__ import annotations

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
            raise ValueError('JSON 顶层必须是对象')

        tokens = payload.get('tokens')
        timestamps = payload.get('timestamps')
        if not isinstance(tokens, list) or not isinstance(timestamps, list):
            raise ValueError('JSON 必须包含数组 tokens 和 timestamps')
        if not tokens or len(tokens) != len(timestamps):
            raise ValueError('tokens 和 timestamps 必须非空且长度一致')

        normalized_timestamps = []
        previous = -1.0
        for index, timestamp in enumerate(timestamps):
            if (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp)
                or timestamp < 0
            ):
                raise ValueError(f'timestamps[{index}] 不是有限的非负数值')
            value = float(timestamp)
            if value < previous:
                raise ValueError('timestamps 必须按非递减顺序排列')
            normalized_timestamps.append(value)
            previous = value

        for index, token in enumerate(tokens):
            if not isinstance(token, str):
                raise ValueError(f'tokens[{index}] 必须是字符串')

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
        console.print('\n[ui.accent]字幕重建[/]')
        console.print(f'[ui.label]文本[/]  [ui.value]{text_file}[/]')
        console.print(f'[ui.label]时间戳[/]  [ui.value]{json_file}[/]')
        
        logger.info('Subtitle rebuild started')
        
        try:
            words = self._load_words(json_file)
            with open(text_file, 'r', encoding='utf-8') as stream:
                text_lines = stream.readlines()
            if not any(line.strip() for line in text_lines):
                raise ValueError('TXT 内容为空')

            output_file, sequence = self._allocate_output(text_file)
            srt_from_txt.generate_srt_file(words, text_lines, output_file)
            if sequence > 1:
                console.print(
                    f'[ui.warning]▲ 同名结果已存在，本次使用编号 '
                    f'({sequence})，未覆盖既有文件[/]'
                )
            console.print(f'[ui.success]✓ 重建完成[/]  [ui.value]{output_file}[/]')
            logger.info('Subtitle rebuild completed')
            return True
        except Exception as e:
            console.print(f'[ui.error]✗ SRT 重建失败[/]  [ui.value]{type(e).__name__}[/]')
            logger.error('Subtitle rebuild failed: error=%s', type(e).__name__)
            return False
