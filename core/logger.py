# coding: utf-8

import os
import logging
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler
from rich.logging import RichHandler
from core.i18n.logging import LocalizedFormatter


class ConsoleFeedbackFilter(logging.Filter):
    """Keep diagnostics in file sinks when a task supplies its own terminal UI."""

    def filter(self, record):
        return not getattr(record, 'console_handled', False)


class TruncatingFileHandler(RotatingFileHandler):
    """Truncate oversized files without creating rotated backups."""

    _TAIL_LINES = 10  # Number of trailing lines retained during truncation.

    def doRollover(self):
        # Read the trailing lines before truncating.
        tail = ''
        try:
            with open(self.baseFilename, 'r', encoding=self.encoding) as f:
                lines = f.readlines()
                tail = ''.join(lines[-self._TAIL_LINES:]).rstrip()
        except Exception:
            pass

        if self.stream:
            self.stream.close()
            self.stream = None
        # Reopen in write mode to truncate the existing file.
        self.stream = open(self.baseFilename, 'w', encoding=self.encoding)
        self.stream.write(f'--- Log truncated at {datetime.now()}\n')
        if tail:
            self.stream.write(f'--- Last {self._TAIL_LINES} lines of previous entries:\n{tail}\n\n')
        self.stream.flush()


class Logger:
    """Configure application logging."""

    _loggers = {}

    @classmethod
    def setup(cls, name: str, log_dir: str = None, level: str = 'INFO', max_bytes: int = 10 * 1024 * 1024, log_filename: str = None):
        """
        Configure and return a logger.

        Args:
            name: Logger name, usually 'server' or 'client'.
            log_dir: Log directory; defaults to logs under the application root.
            level: 'DEBUG', 'INFO', 'WARNING', 'ERROR', or 'CRITICAL'.
            max_bytes: Maximum latest-log size in bytes; default 10 MB.
            backup_count: Legacy compatibility argument.
            log_filename: Optional filename prefix; defaults to name or 'root'.

        Returns:
            logging.Logger: Configured logger.
        """
        # Set the log level.
        file_log_level = getattr(logging, level.upper(), logging.INFO)
        console_log_level = logging.WARNING  # Limit console output to WARNING and above.

        # Update and return an existing logger.
        if name in cls._loggers:
            logger = cls._loggers[name]
            logger.setLevel(min(file_log_level, console_log_level))
            for handler in logger.handlers:
                if isinstance(handler, RotatingFileHandler):
                    handler.setLevel(file_log_level)
                elif isinstance(handler, logging.StreamHandler):
                    handler.setLevel(console_log_level)
            return logger

        # Create the logger.
        logger = logging.getLogger(name if name else None)
        logger.setLevel(min(file_log_level, console_log_level))

        # Do not propagate records to the root logger.
        if name:
            logger.propagate = False

        # Resolve the log directory.
        if log_dir is None:
            from config_client import BASE_DIR
            log_dir = os.path.join(BASE_DIR, 'logs')

        from config_client import ClientConfig
        if getattr(ClientConfig, 'save_diagnostic_logs', True):
            # Create the log directory.
            Path(log_dir).mkdir(parents=True, exist_ok=True)


            # 1. File handler uses the requested level.
            file_name_prefix = log_filename or name or 'root'
            log_file = os.path.join(log_dir, f'{file_name_prefix}_latest.log')
            formatter = logging.Formatter(
                fmt='%(asctime)s.%(msecs)03d %(levelname)-5s [%(filename)20s:%(lineno)-3d] %(message)s',
                datefmt='%H:%M:%S'
            )
            file_handler = TruncatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                encoding='utf-8'
            )
            file_handler.setLevel(file_log_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

            # Keep latest logs for quick diagnosis and separate year/month archives without audio.
            from core.log_archive import DiagnosticArchiveHandler
            try:
                archive = DiagnosticArchiveHandler(
                    Path(log_dir), file_name_prefix,
                    getattr(ClientConfig, 'diagnostic_log_retention_days', 30))
                archive.setFormatter(logging.Formatter(
                    '%(asctime)s %(levelname)s [%(name)s] %(message)s'))
                archive.setLevel(file_log_level)
                logger.addHandler(archive)
            except (OSError, ValueError):
                # Archive failure must not block recording or latest logs; avoid recursive configuration.
                pass

        # 2. Rich console handler uses WARNING and above.
        stream_handler = RichHandler(
            level=console_log_level,
            rich_tracebacks=True,
            markup=True,
            show_path=False
        )
        stream_handler.addFilter(ConsoleFeedbackFilter())
        stream_handler.setFormatter(LocalizedFormatter())
        logger.addHandler(stream_handler)

        # Cache the logger.
        cls._loggers[name] = logger

        return logger

    @classmethod
    def get_logger(cls, name: str):
        """
        Return an existing logger, or create a default logger.

        Args:
            name: Logger name.

        Returns:
            logging.Logger: Logger instance.
        """
        if name not in cls._loggers:
            # Create a default INFO logger if initialization has not run yet.
            # Client/server startup later applies the configured level.
            return cls.setup(name, level='INFO')
        return cls._loggers[name]


# Convenience functions.
def setup_logger(name: str, log_dir: str = None, level: str = 'INFO', **kwargs):
    """Configure a logger through the shared manager."""
    return Logger.setup(name, log_dir, level, **kwargs)


def get_logger(name: str):
    """Get a logger through the shared manager."""
    return Logger.get_logger(name)
