"""
Subtitle rebuilding.
    Recognition output may need spelling
    or sentence-boundary corrections.
    
    Alongside generated SRT,
    save line-oriented TXT and JSON with token timestamps.
    
    Users can correct the TXT and adjust its line breaks,
    then run this helper to rebuild subtitles.
    
    Read timestamps from matching JSON and use the edited TXT lines
    to generate updated SRT.
"""

import sys
from pathlib import Path

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.i18n import tr, initialize_tool_language


import json
from datetime import timedelta
from typing import List, Dict, Union

import typer
import srt
from rich import print
import re 


import difflib

def lines_match_words(text_lines: List[str], words: List) -> List[srt.Subtitle]:
    """
    Align edited lines to token timestamps with SequenceMatcher.
    
    Args:
        text_lines: User-edited subtitle lines.
        words: Original token records with word, start, and end fields.
        
    Returns:
        Aligned srt.Subtitle objects.
    """
    raw_tokens_text = "".join([w['word'] for w in words])
    all_lines_text = "".join([line.strip() for line in text_lines])
    
    # Include all known Chinese and English punctuation in cleanup patterns.
    from core.constants import Punctuation
    punc_pattern = re.compile(rf'[{re.escape(Punctuation.ALL)}\s\d]')
    
    # Map token indexes to character offsets.
    token_chars = []
    token_indices = []
    for i, w in enumerate(words):
        word_clean = punc_pattern.sub('', w['word'].lower())
        for char in word_clean:
            token_chars.append(char)
            token_indices.append(i)
    pure_tokens_text = "".join(token_chars)
    
    # Align the complete text.
    clean_all_lines = punc_pattern.sub('', all_lines_text.lower())
    sm = difflib.SequenceMatcher(None, pure_tokens_text, clean_all_lines)
    matches = sm.get_matching_blocks()
    
    # Map character offsets to word indexes.
    char_to_word_map = {}
    for match in matches:
        for i in range(match.size):
            char_to_word_map[match.b + i] = token_indices[match.a + i]
            
    # Map lines to timestamps.
    subtitle_list = []
    current_char_offset = 0
    last_word_idx = 0
    
    for index, line in enumerate(text_lines):
        line_clean = punc_pattern.sub('', line.lower())
        if not line_clean:
            continue
            
        line_len = len(line_clean)
        found_word_indices = [
            char_to_word_map[i] 
            for i in range(current_char_offset, current_char_offset + line_len) 
            if i in char_to_word_map
        ]
        
        if found_word_indices:
            start_word_idx = min(found_word_indices)
            end_word_idx = max(found_word_indices)
            t1 = words[start_word_idx]['start']
            t2 = words[end_word_idx]['end']
            last_word_idx = end_word_idx
        else:
            t1 = words[min(last_word_idx + 1, len(words)-1)]['start']
            t2 = t1 + 0.5
            
        subtitle = srt.Subtitle(
            index=len(subtitle_list) + 1,
            content=line.strip(),
            start=timedelta(seconds=t1),
            end=timedelta(seconds=t2)
        )
        subtitle_list.append(subtitle)
        current_char_offset += line_len
        
    return subtitle_list



def get_words(json_file: Path) -> list:
    # Read timestamp JSON.
    with open(json_file, 'r', encoding='utf-8') as f:
        json_info = json.load(f)

    # Get timed word records.
    words = [{'word': token.replace('@', ''), 'start': timestamp, 'end': timestamp + 0.2} 
             for (timestamp, token) in zip(json_info['timestamps'], json_info['tokens'])]
    for i in range(len(words) - 1):
        words[i]['end'] = min(words[i]['end'], words[i+1]['start'])
    
    return words


def get_lines(txt_file: Path) -> List[str]:
    # Read edited subtitle lines.
    with open(txt_file, 'r', encoding='utf-8') as f:
        text_lines = f.readlines()
    return text_lines

def generate_srt_file(words: list, text_lines: List[str], srt_file: Path):
    """Generate SRT from words and text_lines."""
    text_lines = [line.rstrip('，。？！,.?!\r\n ') for line in text_lines]
    subtitle_list = lines_match_words(text_lines, words)
    with open(srt_file, 'w', encoding='utf-8') as f:
        f.write(srt.compose(subtitle_list))

def one_task(media_file: Path):
    # Select input files.
    txt_file = media_file.with_suffix('.txt')
    json_file = media_file.with_suffix('.json')
    srt_file = media_file.with_suffix('.srt')
    if (not txt_file.exists()) or (not json_file.exists()):
        print(tr('terminal.srt_from_txt.matching_txt_json_files_not_found_for_skipping', value0=media_file))
        return None

    # Align timed words with edited lines to produce SRT.
    words = get_words(json_file)
    text_lines = get_lines(txt_file)
    
    generate_srt_file(words, text_lines, srt_file)

def main(files: List[Path]):
    for file in files:
        one_task(file)
        print(tr('terminal.srt_from_txt.written', value0=file))

if __name__ == '__main__':
    initialize_tool_language()
    typer.run(main)
        
