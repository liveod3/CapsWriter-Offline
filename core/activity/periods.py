"""Calendar windows shared by runtime, accounting and saved-record summaries."""

from datetime import datetime, time, timedelta, timezone

from core.i18n import Notice


def calendar_window(period, now=None):
    days = {'today': 1, '7d': 7, '30d': 30}.get(period)
    if days is None and period not in ('week', 'month', 'year'):
        raise ValueError(Notice('gui.history_invalid_query'))
    system_local = now is None
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        raise ValueError(Notice('gui.history_invalid_query'))
    if period == 'week':
        first = now.date() - timedelta(days=now.weekday())
    elif period == 'month':
        first = now.date().replace(day=1)
    elif period == 'year':
        first = now.date().replace(month=1, day=1)
    else:
        first = now.date() - timedelta(days=days - 1)
    last = now.date() + timedelta(days=1)
    # Resolve each local midnight separately so system DST transitions are respected.
    def midnight(day):
        value = datetime.combine(day, time.min)
        return value.astimezone() if system_local else value.replace(tzinfo=now.tzinfo)
    start, end = midnight(first), midnight(last)
    return {'period': period, 'date_from': first.isoformat(), 'date_to': now.date().isoformat(),
            'start': start.isoformat(), 'end': end.isoformat(),
            'utc_start': start.astimezone(timezone.utc).isoformat(timespec='microseconds'),
            'utc_end': end.astimezone(timezone.utc).isoformat(timespec='microseconds')}
