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
    """结果处理器：负责文本格式化和文件保存"""

    @staticmethod
    def smart_split(text: str, min_chars: int = 2) -> str:
        """
        智能分行功能
        1. 保留标点符号
        2. 避免在逗号处切分过短的句子
        3. 英文标点需后跟空格才切分（避免 3.14 被切分）
        """
        # 使用捕获组保留标点，英文标点需后跟空白符或结尾
        parts = re.split(r'([，。？]|[.,?!](?:\s+|$))', text)
        lines = []
        buffer = ""
        
        # 强标点（必须换行）
        strong_punct = {'。', '？', '.', '?', '!'}
        punct_chars = set(r'，。？,.?!')
        
        for part in parts:
            clean_part = part.strip()
            # 如果是标点符号（长度为1且在列表中）
            if clean_part and clean_part in punct_chars and len(clean_part) == 1:
                buffer += part
                is_strong = clean_part in strong_punct
                # 如果是强标点，强制换行
                # 如果是弱标点，要累积了一定字数才换行
                if is_strong or len(buffer) > min_chars:
                    lines.append(buffer)
                    buffer = ""
            else:
                # 是文本
                buffer += part
        
        if buffer:
            lines.append(buffer)

            
        return "\n".join(lines)

    @staticmethod
    def output_paths(
        file: Path,
        output_formats: AbstractSet[str],
    ) -> Dict[str, Path]:
        """返回本次任务会生成的整套结果路径。"""
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
        为一整套结果分配共同编号，确保现有结果不会被覆盖。

        原始名称可用时保持不变；发生冲突时依次使用 ``名称 (2)``、
        ``名称 (3)``……，所有启用的输出格式共享同一个编号。
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
        保存转录结果到文件
        
        Returns:
            显示文本、输出编号、实际写入的结果路径列表
        """
        text_display = message.text
        text_accu = message.text_accu if message.text_accu else message.text
        text_split = cls.smart_split(text_accu)
        timestamps = message.timestamps
        tokens = message.tokens
        
        # 为整套输出统一分配一个不会覆盖既有文件的编号。
        _output_file, sequence, paths = cls.allocate_output_file(
            file,
            output_formats,
        )
        
        # 1. 保存 merge.txt
        if 'merge' in output_formats:
            merge_filename = paths['merge']
            with open(merge_filename, 'w', encoding='utf-8') as f:
                f.write(text_accu)
            logger.debug(Notice('diagnostic.result_handler.transcription_output_saved_format_merge'))

        # 2. 保存 txt
        if 'txt' in output_formats:
            txt_filename = paths['txt']
            with open(txt_filename, 'w', encoding='utf-8') as f:
                f.write(text_split)
            logger.debug(Notice('diagnostic.result_handler.transcription_output_saved_format_txt'))

        # 3. 保存 json
        if 'json' in output_formats:
            json_filename = paths['json']
            with open(json_filename, 'w', encoding='utf-8') as f:
                json.dump({'timestamps': timestamps, 'tokens': tokens}, f, ensure_ascii=False)
            logger.debug(Notice('diagnostic.result_handler.transcription_output_saved_format_json'))
        
        # 4. 生成 srt
        if 'srt' in output_formats:
            # 构建 words 信息（无需依赖 json 文件）
            words = [{'word': token.replace('@', ''), 'start': timestamp, 'end': timestamp + 0.2} 
                     for (timestamp, token) in zip(timestamps, tokens)]
            for i in range(len(words) - 1):
                words[i]['end'] = min(words[i]['end'], words[i+1]['start'])
            
            text_lines = text_split.splitlines()
            srt_filename = paths['srt']

            srt_from_txt.generate_srt_file(words, text_lines, srt_filename)

        return text_display, sequence, list(paths.values())
