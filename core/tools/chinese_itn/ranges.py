"""
Range expressions.

Convert adjacent-number range expressions to forms such as 300~500 or 15~16.
"""

from .mappings import value_mapper, unit_mapping, _sorted_units
from .sequence_parser import tokenize, parse_tokens, _BASIC_NUMERIC_TYPES

# Ranges accept basic numeric tokens without decimal points.
_ALLOWED_RANGE_TYPES = _BASIC_NUMERIC_TYPES - {'DOT'}


def _strip_physical_unit(text):
    """Strip a trailing physical unit, such as a person count, meters, or grams."""
    stripped_text = text
    mapped_unit = ''

    for unit_cn in _sorted_units:
        if text.endswith(unit_cn):
            stripped_text = text[:-len(unit_cn)]
            mapped_unit = unit_mapping.get(unit_cn)
            if mapped_unit is None:
                mapped_unit = unit_cn
            break
    return stripped_text, mapped_unit


def parse_range(text):
    """
    Parse a range expression.
    Return a tilde-separated numeric range, such as 300~500 or 15~16,
    or None when parsing fails.
    """
    # Ranges must not contain decimal points.
    if '点' in text:
        return None

    # 1. Strip the trailing physical unit.
    stripped_text, mapped_unit = _strip_physical_unit(text)
    if not stripped_text:
        return None

    # 2. Tokenize and validate.
    tokens = tokenize(stripped_text)
    if not tokens:
        return None
    
    # Accept only basic numeric tokens; reject fraction, percentage, ratio, and OTHER tokens.
    if not all(t.type in _ALLOWED_RANGE_TYPES for t in tokens):
        return None

    # 3. Find consecutive DIGIT runs.
    runs = []
    current_run = []
    start_idx = -1
    for idx, token in enumerate(tokens):
        if token.type == 'DIGIT':
            if not current_run:
                start_idx = idx
            current_run.append(token)
        else:
            if current_run:
                runs.append((start_idx, idx, current_run))
                current_run = []
    if current_run:
        runs.append((start_idx, len(tokens), current_run))

    # 4. Validate the range core.
    # Require exactly one DIGIT run of length two (d1, d2).
    # Reject longer DIGIT runs, which represent digit sequences rather than ranges.
    len2_runs = [run for run in runs if len(run[2]) == 2]
    large_runs = [run for run in runs if len(run[2]) > 2]
    
    if len(len2_runs) != 1 or len(large_runs) > 0:
        return None

    # Extract the range core.
    core_start_idx, core_end_idx, core_tokens = len2_runs[0]
    d1, d2 = core_tokens[0], core_tokens[1]
    v1, v2 = d1.value, d2.value

    # Require v1 < v2 with a difference of one, or the special pair 3 and 5.
    if not (v1 < v2 and (v2 - v1 == 1 or (v1 == 3 and v2 == 5))):
        return None

    # Split tokens into base, core, and suffix.
    base_tokens = tokens[:core_start_idx]
    suffix_tokens = tokens[core_end_idx:]

    # 5. Select the conversion branch.
    if not base_tokens:
        # Case A: no base value (patterns 1 and 3).
        if not suffix_tokens:
            # Pattern 3: adjacent digits become a range, such as 3~4.
            return f"{v1}~{v2}{mapped_unit}"
        else:
            # Pattern 1: apply the suffix magnitude to both range endpoints.
            # The first suffix must be a magnitude unit.
            unit_token = suffix_tokens[0]
            if unit_token.type not in ('TEN', 'HUNDRED', 'THOUSAND', 'TEN_THOUSAND', 'HUNDRED_MILLION'):
                return None
            
            # Remaining suffixes must be ten-thousand or hundred-million units.
            suffix_unit_tokens = suffix_tokens[1:]
            if not all(t.type in ('TEN_THOUSAND', 'HUNDRED_MILLION') for t in suffix_unit_tokens):
                return None
            
            unit = unit_token.char
            suffix_unit = "".join(t.char for t in suffix_unit_tokens)

            if unit == '十':
                return f"{v1 * 10}~{v2 * 10}{suffix_unit}{mapped_unit}"
            elif unit in ('万', '亿'):
                return f"{v1}~{v2}{unit}{suffix_unit}{mapped_unit}"
            elif unit == '千' and suffix_unit:
                return f"{v1}~{v2}{unit}{suffix_unit}{mapped_unit}"
            else:
                mult = unit_token.value
                return f"{v1 * mult}~{v2 * mult}{suffix_unit}{mapped_unit}"
    else:
        # Case B: add a nonempty base to both endpoints, such as 15~16 or 120~130.
        base_str = "".join(t.char for t in base_tokens)
        
        # Parse base_tokens into base_value.
        try:
            base_vals = parse_tokens(base_tokens)
            if not base_vals or len(base_vals) != 1:
                return None
            base_value = int(base_vals[0])
        except Exception:
            return None

        # Parse the suffix.
        if suffix_tokens and suffix_tokens[0].type == 'TEN':
            # A 120~130 expression has a tens suffix.
            # A larger expression can add a ten-thousand suffix after tens.
            suffix_unit_tokens = suffix_tokens[1:]
            if not all(t.type in ('TEN_THOUSAND', 'HUNDRED_MILLION') for t in suffix_unit_tokens):
                return None
            
            multiplier = 10
            suffix_str = "".join(t.char for t in suffix_unit_tokens)
        else:
            # A 15~16 expression has no suffix.
            # A 450000~460000 expression has a ten-thousand suffix.
            if not all(t.type in ('TEN_THOUSAND', 'HUNDRED_MILLION') for t in suffix_tokens):
                return None
            
            last_char = base_str[-1]
            if last_char not in value_mapper:
                return None
            multiplier = value_mapper.get(last_char, 10) // 10
            suffix_str = "".join(t.char for t in suffix_tokens)

        val1 = base_value + v1 * multiplier
        val2 = base_value + v2 * multiplier
        return f"{val1}~{val2}{suffix_str}{mapped_unit}"


def is_range_expression(text):
    """Return whether the input is a range expression."""
    return parse_range(text) is not None


def convert_range_expression(text):
    """Convert a range expression."""
    res = parse_range(text)
    return res if res is not None else text
