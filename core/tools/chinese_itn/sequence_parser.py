# coding: utf-8
"""
Parse Chinese numeric sequences with a shared lexer and parser.
"""

import re
from dataclasses import dataclass
from .mappings import value_mapper as _CHAR_VALUE_MAP

# ============================================================
# Token definitions.
# ============================================================

@dataclass
class Token:
    type: str     # 'DIGIT' | 'TEN' | 'HUNDRED' | ...
    value: int    # Numeric value.
    char: str     # Original character.
    pos: int      # Start offset in the source text.


# ============================================================
# Shared lexer.
# ============================================================

_TOKEN_RULES = [
    ('PERCENT_PREFIX', r'百分之|千分之'),
    ('FRACTION_SEP',   r'分之'),
    ('RATIO_SEP',      r'比'),
    ('DOT',            r'点'),
    ('YEAR_SUF',       r'年'),
    ('MONTH_SUF',      r'月'),
    ('DAY_SUF',        r'日|号'),
    ('MINUTE_SUF',     r'分(?=[零幺一二三四五六七八九十]|秒|$)'), # Lookahead prevents conflicting token matches.
    ('SECOND_SUF',     r'秒'),
    ('ZERO',           r'零'),
    ('DIGIT',          r'[一二两三四五六七八九幺]'),
    ('TEN',            r'十'),
    ('HUNDRED',        r'百'),
    ('THOUSAND',       r'千'),
    ('TEN_THOUSAND',   r'万'),
    ('HUNDRED_MILLION',r'亿'),
    ('WHITESPACE',     r'\s+'),
    ('OTHER',          r'.'),
]

_lex_regex = re.compile('|'.join(f'(?P<{name}>{pattern})' for name, pattern in _TOKEN_RULES))

def tokenize(text):
    """Tokenize input into a shared token sequence."""
    tokens = []
    for match in _lex_regex.finditer(text):
        token_type = match.lastgroup
        token_char = match.group()
        token_pos = match.start()
        
        # Ignore whitespace tokens.
        if token_type == 'WHITESPACE':
            continue
            
        token_value = _CHAR_VALUE_MAP.get(token_char, 0)
        tokens.append(Token(type=token_type, value=token_value, char=token_char, pos=token_pos))
    return tokens


# ============================================================
# Parser.
# ============================================================

# Basic numeric token types.
_BASIC_NUMERIC_TYPES = {
    'DIGIT', 'TEN', 'HUNDRED', 'THOUSAND', 'TEN_THOUSAND', 'HUNDRED_MILLION', 'ZERO', 'DOT'
}

# Magnitude units used to infer omitted trailing units.
_UNIT_TYPES = frozenset({'HUNDRED', 'THOUSAND', 'TEN_THOUSAND'})
# Exclude TEN, the smallest magnitude, because it has no lower implied unit.

def _infer_omitted_unit_scale(tokens, i, j):
    """Infer the multiplier for an omitted trailing unit.

    A digit after thousands can imply hundreds, as in the value 1800.
    The final digit in a 12500-style expression can likewise imply hundreds.
    Return the multiplier, or None when it cannot be inferred.
    """
    prev = tokens[j - 1] if j > i else None
    if prev and prev.type in _UNIT_TYPES and (j + 1 >= len(tokens) or tokens[j + 1].type == 'DOT'):
        return prev.value // 10
    return None


def _parse_atomic(tokens, i):
    """Parse an atomic value at i; return (value, tokens_consumed) or None."""
    n = len(tokens)
    if i >= n:
        return None

    t = tokens[i]

    # Starts with DIGIT.
    if t.type == 'DIGIT':
        d = t.value

        # DIGIT followed by a hundred-million unit: d * 10**8.
        if i + 1 < n and tokens[i+1].type == 'HUNDRED_MILLION':
            return (d * 100000000, 2)

        # DIGIT followed by a ten-thousand unit: d * 10000.
        if i + 1 < n and tokens[i+1].type == 'TEN_THOUSAND':
            return (d * 10000, 2)

        # DIGIT followed by a thousand unit.
        if i + 1 < n and tokens[i+1].type == 'THOUSAND':
            return (d * 1000, 2)

        # DIGIT followed by a hundred unit.
        if i + 1 < n and tokens[i+1].type == 'HUNDRED':
            base = d * 100
            consumed = 2
            j = i + 2
            if j < n and tokens[j].type == 'ZERO':
                if j + 1 < n and tokens[j+1].type == 'DIGIT':
                    base += tokens[j+1].value
                    consumed += 2
            elif j < n and tokens[j].type == 'DIGIT':
                tens_d = tokens[j].value
                if j + 1 < n and tokens[j+1].type == 'TEN':
                    base += tens_d * 10
                    consumed += 2
                    j += 2
                    if j < n and tokens[j].type == 'DIGIT':
                        base += tokens[j].value
                        consumed += 1
                else:
                    base += tens_d * 10
                    consumed += 1
            return (base, consumed)

        # DIGIT + TEN + DIGIT → d*10 + d2
        if (i + 2 < n
            and tokens[i+1].type == 'TEN'
            and tokens[i+2].type == 'DIGIT'):
            if i + 3 < n and tokens[i+3].type == 'TEN':
                pass  # Prefer splitting before a following TEN token.
            else:
                return (10 * d + tokens[i+2].value, 3)

        # DIGIT + TEN becomes d * 10 unless preceded by a DIGIT run.
        if i + 1 < n and tokens[i+1].type == 'TEN':
            if i > 0 and tokens[i-1].type == 'DIGIT':
                pass
            else:
                return (10 * d, 2)

        return (d, 1)

    # Starts with TEN.
    if t.type == 'TEN':
        if (i + 2 < n
            and tokens[i+1].type == 'DIGIT'
            and tokens[i+2].type == 'HUNDRED_MILLION'):
            return ((10 + tokens[i+1].value) * 100000000, 3)
        if i + 1 < n and tokens[i+1].type == 'HUNDRED_MILLION':
            return (10 * 100000000, 2)
        if i + 1 < n and tokens[i+1].type == 'DIGIT':
            return (10 + tokens[i+1].value, 2)
        return (10, 1)

    # === ZERO ===
    if t.type == 'ZERO':
        return (0, 1)

    # Standalone magnitude units.
    if t.type in ('HUNDRED', 'THOUSAND', 'TEN_THOUSAND', 'HUNDRED_MILLION'):
        return (t.value, 1)

    return None


def _build_number(tokens, i):
    """Parse a complete value at i; return (value, tokens_consumed) or None."""
    result = _parse_atomic(tokens, i)
    if result is None:
        return None
    value, consumed = result
    n = len(tokens)
    j = i + consumed

    # Multiply values followed by ten-thousand or hundred-million units.
    if j < n:
        nxt = tokens[j]
        if nxt.type == 'TEN_THOUSAND' and isinstance(value, int) and 0 < value < 10000:
            value *= 10000
            consumed += 1
            j += 1
        elif nxt.type == 'HUNDRED_MILLION' and isinstance(value, int) and 0 < value < 100000000:
            value *= 100000000
            consumed += 1
            j += 1

    # Add lower places after large magnitude units.
    if value >= 10000:
        limit = 10000
    elif value >= 1000:
        limit = 1000
    else:
        limit = None

    if limit:
        while j < n:
            if tokens[j].type == 'ZERO':
                consumed += 1
                j += 1
                continue
            chunk = _parse_atomic(tokens, j)
            if chunk is None:
                break
            chunk_val, chunk_con = chunk
            if chunk_val >= limit:
                break

            # Infer omitted trailing units in expressions such as 1800 or 12500.
            scale = _infer_omitted_unit_scale(tokens, i, j)
            if scale is not None:
                chunk_val = chunk_val * scale

            value += chunk_val
            consumed += chunk_con
            j += chunk_con

    # Apply multipliers after accumulation.
    if j < n:
        nxt = tokens[j]
        if nxt.type == 'TEN_THOUSAND' and isinstance(value, int) and 0 < value < 10000:
            value *= 10000
            consumed += 1
        elif nxt.type == 'HUNDRED_MILLION' and isinstance(value, int) and 0 < value < 100000000:
            value *= 100000000
            consumed += 1

    return (value, consumed)


def parse_tokens(tokens):
    """
    Reduce tokens to a list of Arabic numeral strings.
    Return None if any token is outside the basic numeric set.
    """
    if not tokens:
        return None
        
    # Validate basic numeric tokens.
    if not all(t.type in _BASIC_NUMERIC_TYPES for t in tokens):
        return None

    numbers = []
    i = 0
    n = len(tokens)

    while i < n:
        result = _build_number(tokens, i)
        if result is None:
            return None
        value, consumed = result

        # Reduce decimal points.
        if i + consumed < n and tokens[i + consumed].type == 'DOT':
            dot_idx = i + consumed
            k = dot_idx + 1
            decimal_digits = []
            while k < n and tokens[k].type in ('DIGIT', 'ZERO'):
                decimal_digits.append(str(tokens[k].value))
                k += 1
            if decimal_digits:
                value = f"{value}.{''.join(decimal_digits)}"
                consumed = k - i
            else:
                value = f"{value}."
                consumed += 1

        numbers.append(value)
        i += consumed

    return numbers


# ============================================================
# Public interface.
# ============================================================

def parse_sequence(text):
    """
    Parse a numeric sequence into space-separated values, or None on failure.
    Strip a trailing unit, apply its mapping, and restore it after parsing.
    """
    from .utils import strip_unit
    stripped, unit = strip_unit(text)

    # Do not strip magnitude multipliers as physical units.
    if unit in ('万', '亿'):
        stripped = text
        unit = ''

    if not stripped:
        return None

    tokens = tokenize(stripped)
    if not tokens:
        return None

    # Unknown characters become OTHER tokens. Strip trailing OTHER tokens
    # recursively to support unknown unit suffixes.
    # This preserves the previous parser's trailing-truncation fallback.
    if tokens[-1].type == 'OTHER':
        end_idx = len(tokens)
        while end_idx > 0 and tokens[end_idx - 1].type == 'OTHER':
            end_idx -= 1
        
        # Save trailing OTHER tokens as the unit suffix.
        other_tokens = tokens[end_idx:]
        unit_from_other = "".join(t.char for t in other_tokens)
        tokens = tokens[:end_idx]
        unit = unit_from_other + unit

    if not tokens:
        return None

    # Preserve a trailing large-magnitude unit when display mode treats it as a suffix.
    if tokens and tokens[-1].type in ('TEN_THOUSAND', 'HUNDRED_MILLION'):
        display_unit = tokens[-1].char
        numbers = parse_tokens(tokens[:-1])
        if numbers is not None:
            result = ' '.join(str(n) for n in numbers) + display_unit
            if unit:
                result += unit
            return result

    numbers = parse_tokens(tokens)
    if numbers is None:
        return None

    result = ' '.join(str(n) for n in numbers)
    if unit:
        result += unit
    return result
