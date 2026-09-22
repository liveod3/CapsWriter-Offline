# coding: utf-8

from core.i18n import Notice
import re
import json
from pathlib import Path
from typing import AbstractSet, Dict

from core.tools import srt_from_txt
from core.protocol import RecognitionMessage
from . import logger

class ResultHandler:
    """Format transcription results and save output files."""

    @staticmethod
    def smart_split(text: str, min_chars: int = 2) -> str:
        """
        Split text into subtitle lines:
        1. Retain punctuation.
        2. Avoid short lines at commas.
        3. Require whitespace after English punctuation to preserve decimals such as 3.14.
        """
        # Capture punctuation; English marks require following whitespace or end of text.
        parts = re.split(r'([，。？]|[.,?!](?:\s+|$))', text)
        lines = []
        buffer = ""
        
        # Sentence-ending punctuation always breaks the line.
        strong_punct = {'。', '？', '.', '?', '!'}
        punct_chars = set(r'，。？,.?!')
        
        for part in parts:
            clean_part = part.strip()
            # Identify a single punctuation character.
            if clean_part and clean_part in punct_chars and len(clean_part) == 1:
                buffer += part
                is_strong = clean_part in strong_punct
                # Force a break at sentence-ending punctuation.
                # Break at weaker punctuation only after accumulating enough text.
                if is_strong or len(buffer) > min_chars:
                    lines.append(buffer)
                    buffer = ""
            else:
                # Text segment.
                buffer += part
        
        if buffer:
            lines.append(buffer)

            
        return "\n".join(lines)

    @staticmethod
    def output_paths(
        file: Path,
        output_formats: AbstractSet[str],
    ) -> Dict[str, Path]:
        """Return all output paths for this run."""
        paths = {}
        if 'merge' in output_formats:
            paths['merge'] = file.with_suffix('.merge.txt')
        if 'txt' in output_formats:
            paths['txt'] = file.with_suffix('.txt')
        if 'json' in output_formats:
            paths['json'] = file.with_suffix('.json')
        if 'srt' in output_formats:
            paths['srt'] = file.with_suffix('.srt')
        return paths

    @classmethod
    def allocate_output_file(
        cls,
        source_file: Path,
        output_formats: AbstractSet[str],
    ) -> tuple[Path, int, Dict[str, Path]]:
        """
        Allocate one shared suffix for all outputs without replacing existing files.

        Keep the original name when available; otherwise try ``name (2)``,
        ``name (3)``, and so on for every enabled format.
        """
        sequence = 1
        while True:
            candidate = source_file if sequence == 1 else source_file.with_name(
                f'{source_file.stem} ({sequence}){source_file.suffix}'
            )
            paths = cls.output_paths(candidate, output_formats)
            if not any(path.exists() for path in paths.values()):
                return candidate, sequence, paths
            sequence += 1

    @classmethod
    def save_results(
        cls,
        file: Path,
        message: RecognitionMessage,
        *,
        output_formats: AbstractSet[str],
    ) -> tuple[str, int, list[Path]]:
        """
        Save transcription results.
        
        Returns:
            Display text, output suffix, and paths actually written.
        """
        text_display = message.text
        text_accu = message.text_accu if message.text_accu else message.text
        text_split = cls.smart_split(text_accu)
        timestamps = message.timestamps
        tokens = message.tokens
        
        # Choose one available suffix for the entire output set.
        _output_file, sequence, paths = cls.allocate_output_file(
            file,
            output_formats,
        )
        
        # 1. Save merge.txt.
        if 'merge' in output_formats:
            merge_filename = paths['merge']
            with open(merge_filename, 'w', encoding='utf-8') as f:
                f.write(text_accu)
            logger.debug(Notice('diagnostic.result_handler.transcription_output_saved_format_merge'))

        # 2. Save TXT.
        if 'txt' in output_formats:
            txt_filename = paths['txt']
            with open(txt_filename, 'w', encoding='utf-8') as f:
                f.write(text_split)
            logger.debug(Notice('diagnostic.result_handler.transcription_output_saved_format_txt'))

        # 3. Save JSON.
        if 'json' in output_formats:
            json_filename = paths['json']
            with open(json_filename, 'w', encoding='utf-8') as f:
                json.dump({'timestamps': timestamps, 'tokens': tokens}, f, ensure_ascii=False)
            logger.debug(Notice('diagnostic.result_handler.transcription_output_saved_format_json'))
        
        # 4. Generate SRT.
        if 'srt' in output_formats:
            # Build word records without reading a JSON file.
            words = [{'word': token.replace('@', ''), 'start': timestamp, 'end': timestamp + 0.2} 
                     for (timestamp, token) in zip(timestamps, tokens)]
            for i in range(len(words) - 1):
                words[i]['end'] = min(words[i]['end'], words[i+1]['start'])
            
            text_lines = text_split.splitlines()
            srt_filename = paths['srt']

            srt_from_txt.generate_srt_file(words, text_lines, srt_filename)

        return text_display, sequence, list(paths.values())
