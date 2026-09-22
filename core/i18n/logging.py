"""Localize console records while leaving shared records and file sinks intact."""

from copy import copy
import logging

from . import get_language, localized_value


class LocalizedFormatter(logging.Formatter):
    def format(self, record):
        localized = copy(record)
        locale = get_language()
        localized.msg = localized_value(record.msg, locale=locale)
        if isinstance(record.args, tuple):
            localized.args = tuple(localized_value(value, locale=locale) for value in record.args)
        elif isinstance(record.args, dict):
            localized.args = {key: localized_value(value, locale=locale) for key, value in record.args.items()}
        return super().format(localized)
