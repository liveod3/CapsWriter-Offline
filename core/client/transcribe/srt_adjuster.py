# coding: utf-8
"""
SRT rebuilding.

Use SrtAdjuster to rebuild subtitle timing.
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
    SRT adjuster.
    
    Rebuild subtitles from edited text.
    """
    
    @staticmethod
    def _load_words(json_file: Path) -> list[dict]:
        """Read and validate token timestamps required for subtitle rebuilding."""
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
        """Allocate a rebuilt SRT suffix without replacing existing results."""
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
        Rebuild SRT from explicitly selected TXT and JSON files.
        
        Args:
            text_file: Manually corrected text file.
            json_file: JSON containing tokens and timestamps.
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
