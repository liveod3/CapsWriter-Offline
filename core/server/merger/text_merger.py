# coding: utf-8
"""
Text overlap merging.

Merge text independently of timestamps.
Use difflib to find an alignment within bounded tail/head windows.
"""

from __future__ import annotations

from core.i18n import Notice
import difflib
from core.constants import Punctuation
from . import logger


def merge_by_text(
    prev_text: str,
    new_text: str,
    *_args,
    **_kwargs,
) -> str:
    """
    Merge text using overlapping content.

    Find the best alignment between the previous tail and the new head.
    Rank matching blocks by length and position.
    Require the previous match to end in the final quarter of the tail.
    """
    if not prev_text:
        return new_text
    if not new_text:
        return prev_text

    # 1. Strip boundary punctuation before matching.
    prev_clean = prev_text.rstrip(Punctuation.ALL)

    new_start = 0
    while new_start < len(new_text) and new_text[new_start] in Punctuation.ALL:
        new_start += 1
    new_clean = new_text[new_start:]

    if not prev_clean or not new_clean:
        return prev_text + new_text

    # 2. Align the previous tail and new head.
    # Search the last 100 previous characters and first 100 new characters.
    tail = prev_clean[-100:]
    head = new_clean[:100]

    best = _find_best_overlap(tail, head)

    # Minimum match length.
    if best is None:
        logger.debug(Notice('diagnostic.text_merger.text_merge_no_overlap_found_appending_directly'))
        return prev_text + new_text

    match_pos_in_tail, match_pos_in_head, match_len = best

    # 3. Join at the match boundary.
    # Retain the previous text through the match and append the new text after it.
    keep_prev_len = len(prev_clean) - len(tail) + match_pos_in_tail + match_len
    skip_new_len = match_pos_in_head + match_len

    res_prev = prev_clean[:keep_prev_len]
    res_new = new_text[new_start + skip_new_len:]

    discarded_prev = len(prev_clean) - keep_prev_len - match_len
    logger.debug(
        Notice('diagnostic.text_merger.text_merged_match_previous_tail_removed_new_prefix', value0=match_len, value1=discarded_prev, value2=skip_new_len)
    )
    return res_prev + res_new


def _find_best_overlap(tail: str, head: str) -> tuple[int, int, int] | None:
    """
    Find the best alignment between the previous tail and new head.

    Require the match to end in the tail's final quarter and start in the head's first quarter.
    Prefer matches near the tail's end and the head's start.
    """
    min_match = 2

    sm = difflib.SequenceMatcher(None, tail, head, autojunk=False)
    matches = sm.get_matching_blocks()

    # Position constraints:
    # The tail match must end in its final quarter.
    # The head match must start in its first quarter.
    tail_end_threshold = len(tail) // 4 * 3
    head_start_threshold = len(head) // 4

    candidates = [
        (a, b, size) for a, b, size in matches
        if size >= min_match and a + size > tail_end_threshold and b <= head_start_threshold
    ]
    if not candidates:
        return None

    # Score by length, then position.
    # Squared length favors long matches; +a favors later tails and -b earlier heads.
    def score(item):
        a, b, size = item
        return size * size + a - b

    return max(candidates, key=score)
