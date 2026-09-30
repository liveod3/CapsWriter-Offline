"""Validate per-batch file settings without modifying saved client configuration."""

import math

from core.i18n import Notice
from core.protocol import MAX_AUDIO_MESSAGE_BYTES, MAX_LANGUAGE_LENGTH, MAX_SEG_OVERLAP


DEFAULTS = {
    'language': 'auto',
    'file_seg_duration': 60.0,
    'file_seg_overlap': 4.0,
    'file_max_inflight_chunks': 4,
    'file_io_timeout': 60.0,
    'file_result_timeout': 600.0,
}
MAX_CHUNK_SECONDS = MAX_AUDIO_MESSAGE_BYTES / (16000 * 4)
RANGES = {
    'file_seg_duration': (0.1, MAX_CHUNK_SECONDS),
    'file_seg_overlap': (0.0, MAX_SEG_OVERLAP),
    'file_max_inflight_chunks': (1, 32),
    'file_io_timeout': (1.0, 3600.0),
    'file_result_timeout': (1.0, 86400.0),
}


def validate_options(values, base=None):
    """Validate complete effective settings, including upload flow-control limits."""
    if not isinstance(values, dict) or values.keys() - DEFAULTS.keys():
        raise ValueError(Notice('files.invalid_options'))
    result = dict(DEFAULTS)
    result.update({key: value for key, value in (base or {}).items() if key in DEFAULTS})
    result.update(values)
    language = result['language']
    if (not isinstance(language, str) or not 0 < len(language) <= MAX_LANGUAGE_LENGTH
            or any(ord(char) < 32 for char in language)):
        raise ValueError(Notice('files.invalid_options'))
    for key, (lower, upper) in RANGES.items():
        value = result[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not lower <= value <= upper):
            raise ValueError(Notice('files.invalid_option', name=Notice('files.option.' + key), lower=lower, upper=upper))
    if type(result['file_max_inflight_chunks']) is not int:
        raise ValueError(Notice('files.invalid_options'))
    segment = result['file_seg_duration']
    overlap = result['file_seg_overlap']
    if overlap >= segment:
        raise ValueError(Notice('files.invalid_overlap'))
    if result['file_max_inflight_chunks'] * segment < segment + 2 * overlap:
        raise ValueError(Notice('files.invalid_window'))
    return result
