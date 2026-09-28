"""Bounded, read-only queries over the existing daily Markdown archives."""

from calendar import monthrange
from collections import deque
from datetime import date
import hashlib
import os
import re

from core.i18n import Notice


PAGE_SIZE = 30
DAY_BYTES = 2 * 1024 * 1024
QUERY_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 10000
DETAIL_CHARS = 64000
MAX_PATHS = 20000
HEADER = re.compile(rb'^### ((?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9])[ \t]*\r?$', re.MULTILINE)


def day_path(directory, day):
    if not isinstance(day, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
        raise ValueError(Notice('gui.history_invalid_date'))
    try:
        moment = date.fromisoformat(day)
    except ValueError:
        raise ValueError(Notice('gui.history_invalid_date')) from None
    root = directory.resolve()
    path = root / f'{moment:%Y/%m/%d}.md'
    if not path.resolve().is_relative_to(root):
        raise ValueError(Notice('gui.history_unavailable'))
    return path


def query_days(directory, month, date_from, date_to):
    """Discover only the three-level archive layout, with a metadata-work budget."""
    if month is not None:
        if not isinstance(month, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}', month):
            raise ValueError(Notice('gui.history_invalid_month'))
        try:
            first = date.fromisoformat(month + '-01')
        except ValueError:
            raise ValueError(Notice('gui.history_invalid_month')) from None
        return [f'{month}-{day:02d}' for day in range(monthrange(first.year, first.month)[1], 0, -1)], False, 0
    for value in (date_from, date_to):
        if value is not None:
            day_path(directory, value)
    if date_from and date_to and date_from > date_to:
        raise ValueError(Notice('gui.history_invalid_range'))
    days, pending, scanned, skipped = [], [(directory, ())], 0, 0
    root = directory.resolve()
    while pending:
        parent, parts = pending.pop()
        try:
            if not parent.resolve().is_relative_to(root):
                skipped += 1
                continue
            with os.scandir(parent) as children:
                for child in children:
                    scanned += 1
                    if scanned > MAX_PATHS:
                        return sorted(days, reverse=True), True, skipped
                    pattern = r'[0-9]{4}' if not parts else r'[0-9]{2}' if len(parts) == 1 else r'[0-9]{2}\.md'
                    if not re.fullmatch(pattern, child.name):
                        continue
                    if len(parts) < 2:
                        prefix = '-'.join((*parts, child.name))
                        if ((date_from and prefix < date_from[:len(prefix)])
                                or (date_to and prefix > date_to[:len(prefix)])):
                            continue
                        if child.is_dir():
                            pending.append((parent / child.name, (*parts, child.name)))
                    else:
                        day = '-'.join((*parts, child.name[:2]))
                        try:
                            date.fromisoformat(day)
                        except ValueError:
                            continue
                        if (not date_from or day >= date_from) and (not date_to or day <= date_to):
                            days.append(day)
        except FileNotFoundError:
            continue
        except OSError:
            skipped += 1
    return sorted(days, reverse=True), False, skipped


def query_history(directory, month=None, keyword='', page=0, *, date_from=None, date_to=None):
    if (not isinstance(keyword, str) or len(keyword) > 200 or type(page) is not int
            or not 0 <= page <= MAX_ENTRIES // PAGE_SIZE):
        raise ValueError(Notice('gui.history_invalid_query'))
    needle = keyword.strip().casefold()
    days, limited, skipped = query_days(directory, month, date_from, date_to)
    entries, scanned, used = [], 0, 0
    for day in days:
        try:
            path = day_path(directory, day)
            with path.open('rb') as stream:
                size = stream.seek(0, 2)
                allowance = min(DAY_BYTES, QUERY_BYTES - used)
                start = max(0, size - allowance)
                stream.seek(start)
                data = stream.read(allowance)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            skipped += 1
            continue
        used += len(data)
        limited |= start > 0
        # A tail may start inside a heading or a UTF-8 character; skip that line.
        if start:
            boundary = data.find(b'\n') + 1
            data, start = data[boundary:], start + boundary
        elif data.startswith(b'\xef\xbb\xbf'):
            data, start = data[3:], 3
        matches = list(deque(HEADER.finditer(data), maxlen=MAX_ENTRIES + 1))
        spans = [(match.start(), matches[i + 1].start() if i + 1 < len(matches) else len(data),
                  match[1].decode('ascii'), match.end()) for i, match in enumerate(matches)]
        # Older/custom daily notes without timestamps remain readable as a day record.
        if not spans and data.strip():
            spans = [(0, len(data), '', 0)]
        for begin, end, clock, body in sorted(spans, key=lambda item: (item[2], item[0]), reverse=True):
            scanned += 1
            if scanned > MAX_ENTRIES:
                limited = True
                break
            raw = data[begin:end]
            if needle and needle not in raw.decode('utf-8-sig', errors='replace').casefold():
                continue
            preview = data[body:min(end, body + 1000)].decode('utf-8', errors='replace').strip()
            preview = re.split(r'\r?\n\s*\r?\n', preview, maxsplit=1)[0]
            preview = ' '.join(preview.split())[:160]
            entries.append({'day': day, 'time': clock, 'preview': preview,
                            'offset': start + begin, 'length': end - begin,
                            'digest': hashlib.sha256(raw).hexdigest()})
        if used >= QUERY_BYTES or scanned > MAX_ENTRIES:
            limited = True
            break
    total = len(entries)
    page = min(page, max(0, (total - 1) // PAGE_SIZE))
    return {'entries': entries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE],
            'total': total, 'page': page, 'page_size': PAGE_SIZE,
            'limited': limited, 'skipped': skipped}


def read_history_entry(directory, day, offset, length, digest):
    path = day_path(directory, day)
    if (type(offset) is not int or offset < 0 or type(length) is not int or not 0 < length <= DAY_BYTES
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise ValueError(Notice('gui.history_invalid_query'))
    try:
        with path.open('rb') as stream:
            stream.seek(offset)
            data = stream.read(length)
    except OSError:
        raise ValueError(Notice('gui.history_unavailable')) from None
    if len(data) != length or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(Notice('gui.history_changed'))
    text = data.decode('utf-8-sig', errors='replace').strip()
    from .history_detail import parse_record
    truncated = len(text) > DETAIL_CHARS
    text = text[:DETAIL_CHARS]
    return {'text': text, 'truncated': truncated, 'stages': parse_record(text, truncated=truncated)}
