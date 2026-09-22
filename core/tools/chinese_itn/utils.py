# coding: utf-8
"""
Character and physical-unit helpers.
"""

import re
from .mappings import unit_mapping, common_units, num_mapper

# Precompiled constant patterns.
_UNIT_PATTERN = re.compile(rf'({common_units})$')
_LETTER_PATTERN = re.compile(r'[a-zA-Z]+$')

def strip_unit(original):
    """Strip a numeric suffix and apply the unit mapping."""
    match = _UNIT_PATTERN.search(original)

    if match:
        unit_cn = match.group(1)
        stripped = original[:match.start()]
        mapped_unit = unit_mapping.get(unit_cn)
        unit = mapped_unit if mapped_unit is not None else unit_cn
    else:
        stripped = original
        unit = ''

    if not unit and stripped:
        letter_match = _LETTER_PATTERN.search(stripped)
        if letter_match:
            unit = letter_match.group()
            stripped = stripped[:letter_match.start()]

    return stripped.strip(), unit


def convert_pure_num(original, strict=False):
    """Map Chinese digit characters to Arabic digits."""
    stripped, unit = strip_unit(original)
    if stripped == '一' and not strict:
        return original
    converted = [num_mapper[c] for c in stripped]
    return ''.join(converted) + unit
