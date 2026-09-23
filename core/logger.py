"""Configure independent client/server diagnostics and localized console output."""

import logging
from pathlib import Path
import re

from rich.logging import RichHandler
from core.diagnostics import BufferedDiagnosticHandler, DiagnosticFileHandler, storage_path
from core.i18n.logging import LocalizedFormatter


class ConsoleFeedbackFilter(logging.Filter):
    def filter(self, record):
        return not getattr(record, 'console_handled', False) and not hasattr(record, 'content')


class Logger:
    _loggers = {}

    @classmethod
    def setup(cls, name, log_dir=None, level='INFO', max_bytes=None, log_filename=None):
        file_level = getattr(logging, level.upper(), logging.INFO)
        if name in cls._loggers:
            logger = cls._loggers[name]
            logger.setLevel(min(file_level, logging.WARNING))
            for handler in logger.handlers:
                if isinstance(handler, BufferedDiagnosticHandler):
                    handler.setLevel(file_level)
            return logger

        if name == 'server':
            from config_server import BASE_DIR, ServerConfig as config
        else:
            from config_client import BASE_DIR, ClientConfig as config
        logger = logging.getLogger(name or 'client')
        logger.setLevel(min(file_level, logging.WARNING))
        logger.propagate = False
        logger.diagnostic_path = None
        logger.diagnostic_include_text = bool(getattr(config, 'diagnostic_include_text', False))
        logger.diagnostic_include_context = bool(getattr(config, 'diagnostic_include_context', False))
        logger.diagnostic_text_max_chars = int(getattr(config, 'diagnostic_text_max_chars', 16000))
        root = Path(log_dir) if log_dir is not None else storage_path(BASE_DIR, getattr(config, 'diagnostic_log_dir', 'logs'))
        if getattr(config, 'save_diagnostic_logs', True):
            component = re.sub(r'[^a-zA-Z0-9_-]', '_', log_filename or name or 'client')
            try:
                sink = DiagnosticFileHandler(
                    root, component,
                    retention_days=getattr(config, 'diagnostic_log_retention_days', 30),
                    max_bytes=max_bytes or int(getattr(config, 'diagnostic_log_file_mb', 10)) * 1024 * 1024,
                    backup_count=getattr(config, 'diagnostic_log_backups', 5),
                    budget_mb=getattr(config, 'diagnostic_log_budget_mb', 200),
                )
                handler = BufferedDiagnosticHandler(sink)
                handler.setLevel(file_level)
                logger.addHandler(handler)
                logger.diagnostic_path = sink.path
            except (OSError, ValueError):
                import sys
                from core.i18n import tr
                try:
                    sys.stderr.write(tr('logging.directory_unavailable') + '\n')
                except Exception:
                    pass
        console = RichHandler(level=logging.WARNING, rich_tracebacks=True, markup=True, show_path=False)
        console.addFilter(ConsoleFeedbackFilter())
        console.setFormatter(LocalizedFormatter())
        logger.addHandler(console)
        cls._loggers[name] = logger
        return logger

    @classmethod
    def get_logger(cls, name):
        return cls._loggers[name] if name in cls._loggers else cls.setup(name)


def setup_logger(name, log_dir=None, level='INFO', **kwargs):
    return Logger.setup(name, log_dir, level, **kwargs)


def get_logger(name):
    return Logger.get_logger(name)


def diagnostic_event(logger, event, *, level=logging.INFO, **fields):
    """Emit a structured event; callers pass only approved metadata fields."""
    identity = {key: fields.pop(key) for key in ('task_id', 'socket_id', 'request_id', 'batch_id') if key in fields}
    logger.log(level, event, extra={'event': event, 'data': fields, 'console_handled': True, **identity})


def log_content(logger, event, *, task_id=None, socket_id=None, request_id=None,
                context=None, **texts):
    """Write explicit text copies only to diagnostic files, never console handlers.

    Text and reference capture have separate switches. Keys, headers and URLs are
    never accepted here. Truncation is explicit, and each field has a hard bound.
    """
    if not getattr(logger, 'diagnostic_path', None) or not getattr(logger, 'diagnostic_include_text', False):
        return
    if not logger.isEnabledFor(logging.INFO):
        return
    allowed = {'asr_text', 'input_text', 'output_text', 'final_text', 'system_prompt', 'formatted_text'}
    content = {}
    limit = min(65536, max(1, getattr(logger, 'diagnostic_text_max_chars', 16000)))
    for key, value in texts.items():
        if key in allowed and isinstance(value, str):
            content[key] = {'text': value[:limit], 'chars': len(value), 'truncated': len(value) > limit}
    if isinstance(context, str) and getattr(logger, 'diagnostic_include_context', False):
        content['context'] = {'text': context[:limit], 'chars': len(context), 'truncated': len(context) > limit}
    logger.info(event, extra={'event': event, 'task_id': task_id, 'socket_id': socket_id,
                             'request_id': request_id, 'content': content, 'console_handled': True})
