# coding: utf-8
"""
ITN configuration and mappings.
"""

from pathlib import Path
import json
import re

# Locate static JSON resources.
_current_dir = Path(__file__).resolve().parent
_units_path = _current_dir / 'resources' / 'units.json'
_idioms_path = _current_dir / 'resources' / 'idioms.json'

# Load physical-unit mappings.
try:
    with open(_units_path, 'r', encoding='utf-8') as _f:
        unit_mapping = json.load(_f)
except Exception:
    # Fall back if loading fails.
    unit_mapping = {}

# Load idiom exclusions.
try:
    with open(_idioms_path, 'r', encoding='utf-8') as _f:
        idioms = json.load(_f)
except Exception:
    idioms = []

# Match longer unit names first.
_sorted_units = sorted(unit_mapping.keys(), key=len, reverse=True)
common_units = '|'.join(f'{re.escape(u)}' for u in _sorted_units)

# Chinese numeral mappings.
num_mapper = {
    '零': '0',  '一': '1',  '幺': '1',  '二': '2',
    '两': '2',  '三': '3',  '四': '4',  '五': '5',
    '六': '6',  '七': '7',  '八': '8',  '九': '9',
    '点': '.',
}

# Map numeral characters to values.
value_mapper = {
    '零': 0,  '一': 1,  '幺': 1,  '二': 2,  '两': 2,  '三': 3,  '四': 4,  '五': 5,
    '六': 6,  '七': 7,  '八': 8,  '九': 9,  "十": 10,  "百": 100,
    "千": 1000,  "万": 10000,  "亿": 100000000,
}

# Exclude approximate expressions containing an indefinite quantity.
fuzzy_regex = re.compile(r'几')
