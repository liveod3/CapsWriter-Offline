# coding: utf-8
"""
Synchronize formatting and ITN changes back into tokens.

Punctuation and ITN can insert or replace text after recognition.
Update tokens too so JSON exports remain consistent with formatted output.

Use SequenceMatcher to compare original and formatted text,
then apply insertions, replacements, and deletions to timed tokens.

Alignment strategy:
- Expand multi-character tokens before character alignment, then merge them again.
  This preserves unchanged letters inside partially replaced words, such as cloud -> Claude.
- Tokenize and preserve all inserted text through _tokenize_replacement,
  including content beyond punctuation.
"""

import difflib
from typing import List, Tuple
from core.constants import Punctuation


# Chinese and English punctuation.
_PUNC_SET = set(Punctuation.ALL)


def _expand_tokens(tokens: List[str], timestamps: List[float]) -> Tuple[List[str], List[float]]:
    """Expand multi-character tokens, inheriting each source token's timestamp."""
    flat_tokens = []
    flat_timestamps = []
    for token, ts in zip(tokens, timestamps):
        for ch in token:
            flat_tokens.append(ch)
            flat_timestamps.append(ts)
    return flat_tokens, flat_timestamps


def _merge_ascii_tokens(tokens: List[str], timestamps: List[float]) -> Tuple[List[str], List[float]]:
    """Merge consecutive ASCII alphanumeric characters back into words.

    Complement _tokenize_replacement:
    - Keep each CJK character separate.
    - Merge consecutive ASCII alphanumeric characters.
    - Keep spaces and punctuation separate.
    """
    merged_tokens = []
    merged_timestamps = []
    buf = []
    buf_ts = []

    def flush():
        if buf:
            merged_tokens.append(''.join(buf))
            merged_timestamps.append(buf_ts[0])  # Use the first character's timestamp.
            buf.clear()
            buf_ts.clear()

    for token, ts in zip(tokens, timestamps):
        if token.isascii() and token.isalnum():
            buf.append(token)
            buf_ts.append(ts)
        else:
            flush()
            merged_tokens.append(token)
            merged_timestamps.append(ts)

    flush()
    return merged_tokens, merged_timestamps


def sync_tokens_from_text(
    raw_tokens: List[str],
    raw_timestamps: List[float],
    formatted_text: str,
) -> Tuple[List[str], List[float]]:
    """
    Apply formatted text changes to the timed token sequence.

    Compare raw_text (joined tokens) with formatted_text through SequenceMatcher:
    - equal: Emit each original token once.
    - insert: Tokenize and retain all inserted text.
    - replace: Replace source tokens with formatted text, including ITN changes.
    - delete: Skip removed source tokens.

    Expand multi-character tokens before alignment so character offsets match
    token indexes, then merge the synchronized characters back into words.
    This keeps unchanged letters when a word is only partly replaced.

    Args:
        raw_tokens: Original tokens.
        raw_timestamps: Corresponding timestamps.
        formatted_text: Text after punctuation and ITN.

    Returns:
        Tuple of synchronized new_tokens and new_timestamps.
    """
    # Phase 1: expand multi-character tokens.
    need_merge = any(len(t) > 1 for t in raw_tokens)
    if need_merge:
        work_tokens, work_timestamps = _expand_tokens(raw_tokens, raw_timestamps)
    else:
        work_tokens, work_timestamps = raw_tokens, raw_timestamps

    raw_text = ''.join(work_tokens)

    if formatted_text == raw_text or not work_tokens:
        return list(raw_tokens), list(raw_timestamps)

    # Phase 2: align with SequenceMatcher.

    # Map raw_text character offsets to token indexes.
    # After expansion, the mapping is [0, 1, 2, ..., n-1].
    char_to_tok: List[int] = []
    for idx, token in enumerate(work_tokens):
        char_to_tok.extend([idx] * len(token))

    sm = difflib.SequenceMatcher(None, raw_text, formatted_text)

    new_tokens: List[str] = []
    new_timestamps: List[float] = []
    emitted: set = set()

    for op, ri1, ri2, fi1, fi2 in sm.get_opcodes():
        if op == 'equal':
            _handle_equal(work_tokens, work_timestamps, char_to_tok,
                          ri1, ri2, new_tokens, new_timestamps, emitted)

        elif op == 'insert':
            _handle_insert(formatted_text, fi1, fi2,
                           new_tokens, new_timestamps, work_timestamps)

        elif op == 'delete':
            _handle_delete(char_to_tok, ri1, ri2, emitted)

        elif op == 'replace':
            _handle_replace(work_tokens, work_timestamps, char_to_tok,
                            formatted_text, fi1, fi2, ri1, ri2,
                            new_tokens, new_timestamps, emitted)

    # Phase 3: merge characters back into words.
    if need_merge:
        new_tokens, new_timestamps = _merge_ascii_tokens(new_tokens, new_timestamps)

    return new_tokens, new_timestamps


# Internal opcode handlers.


def _handle_equal(raw_tokens, raw_timestamps, char_to_tok,
                  ri1, ri2, new_tokens, new_timestamps, emitted):
    """Emit original tokens for an equal span."""
    for ri in range(ri1, ri2):
        ti = char_to_tok[ri]
        if ti not in emitted:
            new_tokens.append(raw_tokens[ti])
            new_timestamps.append(raw_timestamps[ti])
            emitted.add(ti)


def _handle_insert(formatted_text, fi1, fi2,
                   new_tokens, new_timestamps, raw_timestamps):
    """Tokenize and preserve the complete inserted span.

    Use _tokenize_replacement for all inserted content,
    including punctuation and spacing changes.
    """
    text = formatted_text[fi1:fi2]
    if not text:
        return
    ts = new_timestamps[-1] if new_timestamps else (raw_timestamps[0] if raw_timestamps else 0.0)
    for token in _tokenize_replacement(text):
        new_tokens.append(token)
        new_timestamps.append(ts)


def _handle_delete(char_to_tok, ri1, ri2, emitted):
    """Mark deleted source tokens as consumed without emitting them."""
    if ri1 >= ri2:
        return
    ti_start = char_to_tok[ri1]
    ti_end = char_to_tok[ri2 - 1] + 1
    for ti in range(ti_start, ti_end):
        emitted.add(ti)


def _handle_replace(raw_tokens, raw_timestamps, char_to_tok,
                    formatted_text, fi1, fi2, ri1, ri2,
                    new_tokens, new_timestamps, emitted):
    """
    Replace the affected source tokens with new text.

    Support ITN replacements across token boundaries.
    Split new text by character type (CJK, ASCII alphanumeric, or other),
    using the first replaced token's timestamp.
    """
    if ri1 >= ri2:
        return

    # Find the affected source token range.
    ti_start = char_to_tok[ri1]
    ti_end = char_to_tok[ri2 - 1] + 1

    # Mark source tokens as consumed.
    for ti in range(ti_start, ti_end):
        emitted.add(ti)

    # Tokenize replacement text.
    replacement = formatted_text[fi1:fi2]
    if not replacement:
        return

    replace_tokens = _tokenize_replacement(replacement)
    ts = raw_timestamps[ti_start]  # Use the first replaced token's timestamp.
    for rt in replace_tokens:
        new_tokens.append(rt)
        new_timestamps.append(ts)


def _tokenize_replacement(text: str) -> List[str]:
    """
    Split replacement text by character type.

    Follow the Paraformer postprocessing convention:
    - Each CJK character is one token.
    - A consecutive ASCII alphanumeric run is one token.
    - Each other character, including spaces and punctuation, is one token.
    """
    tokens = []
    buf: List[str] = []

    def flush():
        if buf:
            tokens.append(''.join(buf))
            buf.clear()

    for ch in text:
        if ch.isascii() and ch.isalnum():
            buf.append(ch)
        elif ch.isalnum():
            # Keep non-ASCII alphanumeric characters separate.
            flush()
            tokens.append(ch)
        else:
            # Keep spaces and punctuation separate.
            flush()
            tokens.append(ch)

    flush()
    return tokens
