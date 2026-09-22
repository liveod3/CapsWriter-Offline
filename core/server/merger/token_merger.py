# coding: utf-8
"""
Timestamp-aware token merging.

Use SequenceMatcher for character alignment when generating timed subtitles.
"""

from __future__ import annotations

from core.i18n import Notice
import difflib
from typing import List, Tuple
from core.constants import Punctuation
from . import logger


def merge_tokens_by_sequence_matcher(
    prev_tokens: List[str],
    prev_timestamps: List[float],
    new_tokens: List[str],
    new_timestamps: List[float],
    offset: float,
    overlap: float,
    is_first_segment: bool = False
) -> Tuple[List[str], List[float]]:
    """
    Merge tokens through SequenceMatcher.

    Algorithm:
    1. Join previous tail and new head tokens into text.
    2. Find matching blocks and rank them with position constraints.
    3. Map character boundaries back to tokens and merge.

    Args:
        prev_tokens: Previously accumulated tokens.
        prev_timestamps: Previously accumulated global timestamps.
        new_tokens: Tokens from the new segment.
        new_timestamps: Segment-relative timestamps.
        offset: Global start time of the current segment.
        overlap: Overlap duration in seconds.
        is_first_segment: Whether this is the first segment.

    Returns:
        Tuple of merged tokens and timestamps.
    """
    # Convert segment timestamps to global time.
    new_global_timestamps = [t + offset for t in new_timestamps]

    if is_first_segment or not prev_tokens:
        return new_tokens, new_global_timestamps
    if not new_tokens:
        return prev_tokens, prev_timestamps

    # 1. Extract overlap-dependent tail/head windows.
    # Estimate overlap text as duration times roughly five characters per second.
    overlap_char_estimate = max(int(overlap * 5), 20)
    prev_tail_len = min(len(prev_tokens), overlap_char_estimate * 3)
    new_head_len = min(len(new_tokens), overlap_char_estimate * 3)

    prev_tail_text = "".join(prev_tokens[-prev_tail_len:])
    new_head_text = "".join(new_tokens[:new_head_len])

    # 2. Find the best alignment.
    best = _find_best_token_overlap(prev_tail_text, new_head_text)

    if best is None:
        logger.debug(Notice('diagnostic.token_merger.token_merge_no_overlap_found_appending_directly'))
        return _fallback_merge(prev_tokens, prev_timestamps, new_tokens, new_global_timestamps, offset)

    match_pos_prev, match_pos_new, match_len = best

    # 3. Map character boundaries back to token indexes.
    prev_cut = _char_pos_to_token_idx(
        prev_tokens, len(prev_tokens) - prev_tail_len,
        match_pos_prev + match_len  # Keep previous tokens through the match end.
    )
    new_start = _char_pos_to_token_idx(
        new_tokens, 0,
        match_pos_new + match_len  # Start new tokens after the match end.
    )

    # 4. Merge.
    result_tokens = prev_tokens[:prev_cut] + new_tokens[new_start:]
    result_timestamps = prev_timestamps[:prev_cut] + new_global_timestamps[new_start:]

    logger.debug(
        Notice('diagnostic.token_merger.token_merge_match_previous_end_token_new_start', value0=match_len, value1=prev_cut, value2=new_start)
    )

    # 5. Remove repeated adjacent punctuation.
    return _clean_repeated_punct(result_tokens, result_timestamps)


def _find_best_token_overlap(prev_tail: str, new_head: str) -> tuple[int, int, int] | None:
    """
    Align the previous tail and new head using the text_merger strategy.

    Require the match to end in the tail's final quarter and start in the head's first quarter.
    """
    min_match = 2

    sm = difflib.SequenceMatcher(None, prev_tail, new_head, autojunk=False)
    matches = sm.get_matching_blocks()

    tail_end_threshold = len(prev_tail) // 4 * 3
    head_start_threshold = len(new_head) // 4

    candidates = [
        (a, b, size) for a, b, size in matches
        if size >= min_match and a + size > tail_end_threshold and b <= head_start_threshold
    ]
    if not candidates:
        return None

    def score(item):
        a, b, size = item
        return size * size + a - b

    return max(candidates, key=score)


def _char_pos_to_token_idx(tokens: List[str], base_offset: int, char_pos: int) -> int:
    """
    Map a character position to a global token index.

    Count characters from base_offset to the first token boundary at or after char_pos.
    """
    char_count = 0
    for i in range(base_offset, len(tokens)):
        if char_count >= char_pos:
            return i
        char_count += len(tokens[i])
    return len(tokens)


def _fallback_merge(
    prev_tokens, prev_timestamps, new_tokens, new_global_timestamps, offset
) -> Tuple[List[str], List[float]]:
    """Fall back to a timestamp-based join."""
    last_time = prev_timestamps[-1] if prev_timestamps else offset
    new_start_idx = 0
    for i, t in enumerate(new_global_timestamps):
        if t > last_time + 0.1:
            new_start_idx = i
            break
    else:
        new_start_idx = len(new_tokens)

    result_tokens = prev_tokens + new_tokens[new_start_idx:]
    result_timestamps = prev_timestamps + new_global_timestamps[new_start_idx:]

    logger.debug(Notice('diagnostic.token_merger.timestamp_merge_fallback_starting_at_new', value0=new_start_idx))
    return result_tokens, result_timestamps


def _clean_repeated_punct(
    tokens: List[str], timestamps: List[float]
) -> Tuple[List[str], List[float]]:
    """Remove repeated adjacent punctuation."""
    puncs = set(Punctuation.ALL + " ")
    clean_tokens: List[str] = []
    clean_timestamps: List[float] = []
    for token, ts in zip(tokens, timestamps):
        if clean_tokens and token in puncs and clean_tokens[-1] == token:
            continue
        clean_tokens.append(token)
        clean_timestamps.append(ts)
    return clean_tokens, clean_timestamps
