# coding: utf-8
"""
ITN replacement pipeline.
"""

from .mappings import idioms, fuzzy_regex, value_mapper

_DIGIT_CHARS = {k for k, v in value_mapper.items() if v <= 9}
_UNIT_CHARS = {k for k, v in value_mapper.items() if v > 9}
from .patterns import pattern
from .utils import convert_pure_num, strip_unit
from .sequence_parser import parse_sequence, tokenize, parse_tokens, _BASIC_NUMERIC_TYPES
from .ranges import is_range_expression, convert_range_expression


# ============================================================
# Shared helpers.
# ============================================================

def _all_numeric(tokens):
    """Return whether all tokens represent basic numeric values."""
    return all(t.type in _BASIC_NUMERIC_TYPES for t in tokens)


def _reduce_binary_op(tokens, sep_type, fmt):
    """Reduce a binary separated expression for fractions or ratios."""
    indices = [i for i, t in enumerate(tokens) if t.type == sep_type]
    if len(indices) != 1:
        return None
    idx = indices[0]
    left, right = tokens[:idx], tokens[idx+1:]
    if not left or not right or not _all_numeric(left) or not _all_numeric(right):
        return None
    left_vals = parse_tokens(left)
    right_vals = parse_tokens(right)
    if left_vals and len(left_vals) == 1 and right_vals and len(right_vals) == 1:
        return fmt.format(left=left_vals[0], right=right_vals[0])
    return None


# ============================================================
# Grammar reducers.
# ============================================================

def try_reduce_percent(tokens, original):
    """Reduce percentages and per-mille expressions."""
    if not tokens or tokens[0].type != 'PERCENT_PREFIX':
        return None
    val_tokens = tokens[1:]
    if not val_tokens or not _all_numeric(val_tokens):
        return None
    vals = parse_tokens(val_tokens)
    if vals is not None and len(vals) == 1:
        suffix = '%' if tokens[0].char == '百分之' else '‰'
        return f"{vals[0]}{suffix}"
    return None


def try_reduce_fraction(tokens, original):
    """Reduce fractions to numerator/denominator notation."""
    return _reduce_binary_op(tokens, 'FRACTION_SEP', '{right}/{left}')


def try_reduce_ratio(tokens, original):
    """Reduce ratios to colon-separated notation."""
    return _reduce_binary_op(tokens, 'RATIO_SEP', '{left}:{right}')


def try_reduce_time(tokens, original):
    """Reduce time expressions to clock notation."""
    dot_indices = [idx for idx, t in enumerate(tokens) if t.type == 'DOT']
    min_indices = [idx for idx, t in enumerate(tokens) if t.type == 'MINUTE_SUF']
    if len(dot_indices) != 1 or len(min_indices) != 1:
        return None

    dot_idx, min_idx = dot_indices[0], min_indices[0]
    if dot_idx >= min_idx:
        return None

    hour_tokens = tokens[:dot_idx]
    minute_tokens = tokens[dot_idx+1:min_idx]
    second_tokens = tokens[min_idx+1:]

    if not hour_tokens or not minute_tokens or not _all_numeric(hour_tokens) or not _all_numeric(minute_tokens):
        return None

    # Process seconds.
    has_second = False
    sec_str = ""
    if second_tokens:
        if second_tokens[-1].type != 'SECOND_SUF':
            return None
        sec_val_tokens = second_tokens[:-1]
        if not sec_val_tokens or not _all_numeric(sec_val_tokens):
            return None
        sec_vals = parse_tokens(sec_val_tokens)
        if not sec_vals or len(sec_vals) not in (1, 2):
            return None
        if len(sec_vals) == 2 and sec_vals[0] in (0, '0'):
            sec_vals = [sec_vals[1]]
        if len(sec_vals) != 1:
            return None

        val = sec_vals[0]
        if isinstance(val, float):
            int_part, dec_part = str(val).split('.')
            sec_str = f"{int_part.zfill(2)}.{dec_part}"
        else:
            sec_str = str(val).zfill(2)
        has_second = True

    hour_vals = parse_tokens(hour_tokens)
    min_vals = parse_tokens(minute_tokens)
    if min_vals and len(min_vals) == 2 and min_vals[0] in (0, '0'):
        min_vals = [min_vals[1]]
    if hour_vals and len(hour_vals) == 1 and min_vals and len(min_vals) == 1:
        h_str = str(hour_vals[0]).zfill(2)
        m_str = str(min_vals[0]).zfill(2)
        if has_second:
            return f"{h_str}:{m_str}:{sec_str}"
        return f"{h_str}:{m_str}"
    return None


def try_reduce_date(tokens, original):
    """Reduce date expressions while preserving date unit characters."""
    year_indices = [idx for idx, t in enumerate(tokens) if t.type == 'YEAR_SUF']
    month_indices = [idx for idx, t in enumerate(tokens) if t.type == 'MONTH_SUF']
    day_indices = [idx for idx, t in enumerate(tokens) if t.type == 'DAY_SUF']

    if len(year_indices) > 1 or len(month_indices) > 1 or len(day_indices) > 1:
        return None
    if not year_indices and not month_indices and not day_indices:
        return None

    y_idx = year_indices[0] if year_indices else -1
    m_idx = month_indices[0] if month_indices else -1
    d_idx = day_indices[0] if day_indices else -1

    # Require year, month, and day units in order.
    indices = [i for i in (y_idx, m_idx, d_idx) if i != -1]
    if indices != sorted(indices):
        return None

    def _parse_part(start, end):
        """Parse tokens in [start, end) into a numeric string."""
        part = tokens[start:end]
        if not part or not _all_numeric(part):
            return None
        vals = parse_tokens(part)
        if not vals or len(vals) != 1:
            return None
        return str(vals[0])

    last_idx = 0
    res_str = ""

    if y_idx != -1:
        y_tokens = tokens[last_idx:y_idx]
        if not y_tokens or not _all_numeric(y_tokens):
            return None
        if all(t.type in ('DIGIT', 'ZERO') for t in y_tokens):
            y_str = convert_pure_num("".join(t.char for t in y_tokens), strict=True)
        else:
            y_str = _parse_part(last_idx, y_idx)
            if y_str is None:
                return None
        res_str += y_str + "年"
        last_idx = y_idx + 1

    if m_idx != -1:
        m_str = _parse_part(last_idx, m_idx)
        if m_str is None:
            return None
        res_str += m_str + "月"
        last_idx = m_idx + 1

    if d_idx != -1:
        d_str = _parse_part(last_idx, d_idx)
        if d_str is None:
            return None
        res_str += d_str + tokens[d_idx].char
        last_idx = d_idx + 1

    if last_idx != len(tokens):
        return None
    return res_str


def try_reduce_date_time(tokens, original):
    """Reduce dates, times, and combined date/time expressions."""
    idxs = [i for i, t in enumerate(tokens) if t.type in ('DAY_SUF', 'MONTH_SUF', 'YEAR_SUF')]
    split_idx = idxs[-1] if idxs else -1

    date_res = try_reduce_date(tokens[:split_idx+1], None) if split_idx != -1 else ""
    time_res = try_reduce_time(tokens[split_idx+1:], None) if split_idx + 1 < len(tokens) else ""

    return date_res + time_res if (date_res is not None and time_res is not None) else None


def try_reduce_numerical(tokens, original_text):
    """Reduce numeric values and digit sequences."""
    stripped_text, unit = strip_unit(original_text)

    # Keep ten-thousand and hundred-million units as numeric multipliers.
    if unit in ('万', '亿'):
        stripped_text = original_text

    if not stripped_text or stripped_text.endswith('点'):
        return None

    stripped_tokens = tokenize(stripped_text)
    if not stripped_tokens or not _all_numeric(stripped_tokens):
        return None

    # Parse plain digits without magnitude units.
    if all(t.type in ('DIGIT', 'ZERO', 'DOT') for t in stripped_tokens):
        return convert_pure_num(original_text)

    # Apply the general sequence reducer.
    return parse_sequence(original_text)


def try_reduce_range(tokens, original):
    """Reduce explicit ranges to hyphen-separated notation."""
    return convert_range_expression(original) if is_range_expression(original) else None


# ============================================================
# Pipeline entry point.
# ============================================================

def replace(match):
    """Apply grammar reducers to one candidate match."""
    string = match.string
    l_pos, r_pos = match.regs[2]
    l_pos = max(l_pos - 2, 0)
    head = match.group(1)
    original = match.group(2)

    if idioms and any(
        string.find(idiom) in range(l_pos, r_pos) and len(original) <= len(idiom)
        for idiom in idioms
    ):
        final = original

    elif fuzzy_regex.search(original):
        final = original

    elif (_UNIT_CHARS.issuperset(original)
          and len(original) >= 2
          and not any(c in _DIGIT_CHARS for c in original)):
        final = original

    else:
        # Extract the sign.
        sign_prefix = ""
        parsed_original = original
        if original and original[0] in ('正', '负'):
            sign_prefix = '+' if original[0] == '正' else '-'
            parsed_original = original[1:]

        if parsed_original == '一' and sign_prefix:
            final = sign_prefix + '1'
        else:
            tokens = tokenize(parsed_original)

            for reducer in [
                try_reduce_percent,    # Percentages.
                try_reduce_fraction,   # Fractions.
                try_reduce_ratio,      # Ratios.
                try_reduce_date_time,  # Dates and times.
                try_reduce_range,      # Ranges.
                try_reduce_numerical,  # Numeric values.
            ]:
                res = reducer(tokens, parsed_original)
                if res is not None:
                    final = sign_prefix + res
                    break
            else:
                final = original

    if head:
        final = head + final

    return final


def chinese_to_num(original):
    """Convert Chinese number words to Arabic numerals."""
    return pattern.sub(replace, original)
