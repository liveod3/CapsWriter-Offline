# Text overlap merging

Implementation: [text_merger.py](../../core/server/merger/text_merger.py).
This path builds the `text` result without timestamps; timestamp-based merging is a separate path in [merger](../../core/server/merger).

## Current behavior

1. Return the other input when one input is empty.
2. Strip trailing punctuation from the previous text and skip leading punctuation in the new text for matching.
3. Compare the last 100 characters of the previous text with the first 100 of the new text using `difflib.SequenceMatcher(autojunk=False)`.
4. Keep matching blocks of at least two characters whose end in the previous window is beyond `len(tail) // 4 * 3` and whose start in the new window is at most `len(head) // 4`.
5. Select the block with the highest `size * size + previous_position - new_position` score. Keep the previous text through the block and append the new text after it.
6. If no eligible block exists, concatenate the original inputs.

The splice can discard unmatched text after the selected block in the previous input and before it in the new input. Repeated phrases and ASR errors therefore need regression coverage; this is a text heuristic, not phonetic correction.

The windows and thresholds are implementation details, not public configuration fields. Earlier references to `util/constants.py`, `OVERLAP_CHARS = 20` and `ERROR_TOLERANCE = 3` no longer describe the implementation.

Tests: [test_text_processing.py](../../tests/unit/test_text_processing.py). Use synthetic text fixtures; model accuracy requires separate audio evaluation.
