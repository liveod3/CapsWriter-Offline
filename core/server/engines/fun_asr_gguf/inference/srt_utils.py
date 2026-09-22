"""
SRT generation helpers.
"""

import os
from datetime import timedelta
from typing import List, Dict, Any
import srt

def generate_srt_file(
    segments: List[Dict[str, Any]], 
    output_path: str,
    max_chars_per_line: int = 30
):
    """
    Generate SRT from token start timestamps.
    
    Args:
        segments: List of [character, timestamp] entries.
        output_path: Output path.
        max_chars_per_line: Maximum line length.
    """
    if not segments:
        return

    subtitles = []
    
    # Sentence boundary heuristics.
    puncs = set("，。！？；,.!?;")
    pause_threshold = 0.4      # Consider splitting at pauses longer than 0.4 seconds.
    min_chars_to_break = 5    # Require five accumulated characters for ordinary pause splits.
    long_pause_threshold = 1.0 # Force a split after pauses longer than one second.
    
    current_chars = []
    start_time = segments[0][1]
    for i, seg in enumerate(segments):
        char = seg[0]
        time_s = seg[1]
        
        current_chars.append(char)
        
        # Split on any of these conditions:
        # 1. Punctuation.
        # 2. Maximum line length.
        # 3. Final character.
        # 4. A long pause before the next character.
        
        is_punc = char in puncs
        is_last = (i == len(segments) - 1)
        too_long = len(current_chars) >= max_chars_per_line
        
        # Detect pauses.
        has_pause = False
        if not is_last:
            pause_duration = segments[i+1][1] - time_s
            # Require enough text for normal pauses, or split unconditionally for long pauses.
            if (len(current_chars) >= min_chars_to_break and pause_duration > pause_threshold) \
               or (pause_duration > long_pause_threshold):
                has_pause = True
        
        if is_punc or is_last or too_long or has_pause:
            # Determine the end time.
            if is_last:
                end_time = time_s + 0.5
            else:
                next_start = segments[i+1][1]
                end_time = min(time_s + 0.5, (time_s + next_start) / 2)

            content = "".join(current_chars).strip()
            # Remove trailing punctuation from subtitle text.
            content = content.rstrip(''.join(puncs) + ' ')
            
            if content:
                subtitles.append(srt.Subtitle(
                    index=len(subtitles) + 1,
                    start=timedelta(seconds=start_time),
                    end=timedelta(seconds=end_time),
                    content=content
                ))
            
            # Reset line state.
            if not is_last:
                current_chars = []
                start_time = segments[i+1][1]

    # Write the output file.
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(srt.compose(subtitles))

    return output_path

