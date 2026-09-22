"""
FunASR GGUF result merging.

Join and deduplicate segments from long recordings.
"""

from typing import List, Dict, Any, Tuple
from . import logger

import difflib

def merge_transcription_results(
    results: List[Dict[str, Any]], 
    segment_offsets: List[float], 
    overlap_s: float
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Find overlap alignment with SequenceMatcher.
    """
    if not results:
        return "", []
    
    if len(results) == 1:
        offset = segment_offsets[0]
        full_segments = []
        for seg in results[0].get('segments') or []:
            full_segments.append([seg[0], seg[1] + offset])
        return results[0]['text'], full_segments

    full_segments = []
    puncs = set("，。！？；,.!?; ")
    
    for i, res in enumerate(results):
        offset = segment_offsets[i]
        curr_segments = res.get('segments') or []

        if i == 0:
            full_segments.extend([[s[0], s[1] + offset] for s in curr_segments])
            continue

        if not curr_segments:
            continue

        # Find an alignment.
        # Extract the previous tail and new head.
        # Consider previous timestamps starting at offset - 1.0.
        buffer_overlap_segs = [s for s in full_segments if s[1] >= offset - 1.0]
        buffer_overlap_text = "".join([s[0] for s in buffer_overlap_segs])
        
        # Extract the new segment's leading overlap window.
        curr_overlap_limit = overlap_s + 1.0
        curr_overlap_segs = [s for s in curr_segments if s[1] <= curr_overlap_limit]
        curr_overlap_text = "".join([s[0] for s in curr_overlap_segs])
        
        # Find the best alignment through SequenceMatcher.
        sm = difflib.SequenceMatcher(None, buffer_overlap_text, curr_overlap_text)
        match = sm.find_longest_match(0, len(buffer_overlap_text), 0, len(curr_overlap_text))
        
        if match.size >= 2: # Require at least two matching characters.
            # match.a indexes buffer_overlap_text.
            # match.b indexes curr_overlap_text.
            
            # a. Trim the previous buffer.
            # Resolve buffer_overlap_segs[match.a] to the global index.
            target_seg = buffer_overlap_segs[match.a]
            
            # Find the nearest matching target_seg from the end of full_segments.
            try:
                global_idx = -1
                for idx in range(len(full_segments)-1, -1, -1):
                    if full_segments[idx][1] == target_seg[1] and full_segments[idx][0] == target_seg[0]:
                        global_idx = idx
                        break
                
                if global_idx != -1:
                    full_segments = full_segments[:global_idx]
            except:
                pass
            
            # b. Append the new segment from match.b.
            # match.b indexes curr_overlap_text and corresponds to curr_overlap_segs.
            # Resolve its original index in curr_segments.
            match_idx_in_curr = -1
            match_seg = curr_overlap_segs[match.b]
            for idx, s in enumerate(curr_segments):
                if s is match_seg: # Match by object identity.
                    match_idx_in_curr = idx
                    break
            
            if match_idx_in_curr != -1:
                to_add = curr_segments[match_idx_in_curr:]
                full_segments.extend([[s[0], s[1] + offset] for s in to_add])
            else:
                # Fallback for an unmatched object.
                full_segments.extend([[s[0], s[1] + offset] for s in curr_segments])
        else:
            # Fall back to timestamp-based concatenation.
            last_time = full_segments[-1][1] if full_segments else offset
            to_add = [s for s in curr_segments if s[1] + offset > last_time + 0.1]
            full_segments.extend([[s[0], s[1] + offset] for s in to_add])

    # Remove repeated and residual punctuation.
    clean_segments = []
    for s in full_segments:
        if clean_segments and s[0] in puncs and clean_segments[-1][0] == s[0]:
            continue
        clean_segments.append(s)

    full_text = "".join([s[0] for s in clean_segments])
    return full_text, clean_segments
