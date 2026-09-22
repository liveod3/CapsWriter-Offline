# coding=utf-8

from core.i18n import tr
import re
from datetime import timedelta
from typing import List, Optional
import srt
import json
from .schema import TranscriptionResult, RecognitionResult
from .chinese_itn import chinese_to_num as itn

def results_to_srt(items: List[RecognitionResult], max_chars: int = 40) -> str:
    """
    Convert RecognitionResult entries to SRT content.
    Split at commas, sentence punctuation, and newlines.
    """
    if not items:
        return ""

    subtitles = []
    current_texts = []
    start_time = None
    
    # Match Chinese/English punctuation and line breaks.
    split_pattern = re.compile(r'[，。？！、\n]|[,.?!]\s*')
    
    for i, item in enumerate(items):
        if start_time is None:
            start_time = item.start
        
        current_texts.append(item.text)
        current_content = "".join(current_texts)
        
        # Split at punctuation or the maximum line length.
        if split_pattern.search(item.text) or len(current_content) >= max_chars:
            content = current_content.strip()
            if content:
                # Optionally remove trailing punctuation before ITN.
                clean_content = content.rstrip("，。？！、,.?!")
                itn_content = itn(clean_content)
                
                # Estimate the end time.
                end_time_val = items[i+1].start if (i+1) < len(items) else item.start + 0.5
                
                subtitles.append(srt.Subtitle(
                    index=len(subtitles) + 1,
                    start=timedelta(seconds=start_time),
                    end=timedelta(seconds=end_time_val),
                    content=itn_content
                ))
            current_texts = []
            start_time = None
            
    # Process remaining text.
    if current_texts:
        content = "".join(current_texts).strip()
        if content:
            itn_content = itn(content.rstrip("，。？！：、,.?!"))
            end_time_val = items[-1].start + 0.5
            subtitles.append(srt.Subtitle(
                index=len(subtitles) + 1,
                start=timedelta(seconds=start_time),
                end=timedelta(seconds=end_time_val),
                content=itn_content
            ))
            
    return srt.compose(subtitles)

def export_to_srt(path: str, result: TranscriptionResult):
    """Save transcription as SRT."""
    if not result.results:
        with open(path, "w", encoding="utf-8") as f: f.write("")
        return
    
    content = results_to_srt(result.results)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(tr('terminal.exporters.subtitle_file_generated', value0=path))

def export_to_json(path: str, result: TranscriptionResult):
    """Save transcription as JSON timestamp records."""
    data = [
        {
            "text": r.text,
            "start": round(r.start, 3),
        }
        for r in result.results
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(tr('terminal.exporters.timestamps_exported', value0=path))

def export_to_txt(path: str, result: TranscriptionResult):
    """Save transcription as plain text."""
    final_text = itn(result.text)
    # Split lines at punctuation.
    formatted_text = re.sub(r'([，。？！：])', r'\1\n', final_text)
    with open(path, "w", encoding="utf-8") as f:
        f.write(formatted_text)
    print(tr('terminal.exporters.text_file_saved', value0=path))
