"""Localized labels and value formatting for the statistics cards."""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from core.i18n import tr


def field_label(key, role='muted', help_key=None):
    """Keep field definitions on names instead of mixing them with values."""
    label = QLabel(tr('gui.stats_' + key))
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setObjectName(role)
    label.setToolTip(tr(help_key or 'gui.stats_help_' + key))
    label.setCursor(Qt.CursorShape.WhatsThisCursor)
    return label


def number(value):
    return '—' if value is None else f'{value:,.3f}'.rstrip('0').rstrip('.')


def recent_value(recent):
    if not recent:
        return '—', tr('gui.stats_no_recent')
    value = (number(recent['value_ms']) if recent['availability'] == 'observed'
             else tr('activity.availability.' + recent['availability']))
    moment = datetime.fromisoformat(recent['started_at']).astimezone().strftime('%m-%d %H:%M:%S')
    return value, tr('gui.stats_recent_hint', time=moment, outcome=tr('activity.outcome.' + recent['outcome']))
