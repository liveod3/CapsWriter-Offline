# coding=utf-8

from core.i18n import tr
import re
from datetime import timedelta
from typing import List, Optional
import srt
import json
from .schema import ForcedAlignResult, ForcedAlignItem, TranscribeResult
from .chinese_itn import chinese_to_num as itn

def alignment_to_srt(items: Optional[List[ForcedAlignItem]], max_chars: int = 40) -> str:
    """
    Convert alignment results to SRT content.
    Split at commas, sentence punctuation, and newlines.
    """
    if not items:
        return ""

    subtitles = []
    current_texts = []
    start_time = None
    
    # Match Chinese/English punctuation and line breaks.
    # Include whitespace that some ASR engines emit after punctuation.
    split_pattern = re.compile(r'[，。？！、\n]|[,.?!]\s*')
    
    for item in items:
        # Track each subtitle line's start time.
        if start_time is None:
            start_time = item.start_time
        
        current_texts.append(item.text)
        
        # Accumulate text.
        current_content = "".join(current_texts)
        
        # Split when either condition holds:
        # 1. A punctuation boundary is reached.
        # 2. The line exceeds max_chars.
        if split_pattern.search(item.text) or len(current_content) >= max_chars:
            content = current_content.strip()
            if content:
                # Remove trailing punctuation.
                content = content.rstrip("，。？！、,.?!")
                # Apply ITN.
                itn_content = itn(content)
                subtitles.append(srt.Subtitle(
                    index=len(subtitles) + 1,
                    start=timedelta(seconds=start_time),
                    end=timedelta(seconds=item.end_time),
                    content=itn_content
                ))
            current_texts = []
            start_time = None
            
    # Process remaining text.
    if current_texts:
        content = "".join(current_texts).strip()
        if content:
            # Remove trailing punctuation.
            content = content.rstrip("，。？！：、,.?!")
            # Apply ITN.
            itn_content = itn(content)
            end_time = items[-1].end_time
            subtitles.append(srt.Subtitle(
                index=len(subtitles) + 1,
                start=timedelta(seconds=start_time),
                end=timedelta(seconds=end_time),
                content=itn_content
            ))
            
    return srt.compose(subtitles)

def alignment_to_json(items: Optional[List[ForcedAlignItem]]) -> List[dict]:
    """Convert alignment results to serializable dictionaries."""
    if not items:
        return []
    return [
        {
            "text": it.text,
            "start": round(it.start_time, 3),
            "end": round(it.end_time, 3)
        }
        for it in items
    ]

def export_to_srt(path: str, result: TranscribeResult):
    """Save alignment results as SRT."""
    if not result.alignment:
        with open(path, "w", encoding="utf-8") as f: f.write("")
        return
    
    content = alignment_to_srt(result.alignment.items)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(tr('terminal.exporters.subtitle_file_generated', value0=path))

def export_to_json(path: str, result: TranscribeResult):
    """Save alignment results as JSON."""
    if not result.alignment:
        with open(path, "w", encoding="utf-8") as f: f.write("[]")
        return

    data = alignment_to_json(result.alignment.items)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(tr('terminal.exporters.timestamps_exported', value0=path))

def export_to_txt(path: str, result: TranscribeResult):
    """Save transcription as TXT after ITN and punctuation-based line splitting."""
    # 1. Apply ITN.
    final_text = itn(result.text)
    # 2. Split at punctuation while retaining marks.
    formatted_text = re.sub(r'([，。？！：])', r'\1\n', final_text)
    # 3. Also split at comma/period plus space after Latin letters.
    formatted_text = re.sub(r'(?<=[a-zA-Z])([,\.] )', r'\1\n', formatted_text)
    
    with open(path, "w", encoding="utf-8") as f:
        f.write(formatted_text)
    print(tr('terminal.exporters.text_file_saved', value0=path))
