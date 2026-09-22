"""
Toast logging configuration.

Support standalone execution and application logging integration.
"""
import logging
import sys
from core.i18n.logging import LocalizedFormatter


def get_toast_logger(name: str) -> logging.Logger:
    """
    Get a Toast logger.

    Detect existing application loggers:
    - Reuse a configured client or server logger.
    - Otherwise create a standalone console logger.

    Args:
        name: Logger name, usually __name__.

    Returns:
        Configured logger.
    """
    # Check application loggers.
    client_logger = logging.getLogger('client')
    server_logger = logging.getLogger('server')

    if client_logger.handlers:
        # Reuse the configured client logger.
        return client_logger
    elif server_logger.handlers:
        # Reuse the configured server logger.
        return server_logger
    else:
        # Create a logger for standalone execution.
        logger = logging.getLogger(name)

        # Add console output if no handlers exist.
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = LocalizedFormatter(
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
            logger.propagate = False  # Disable propagation in standalone mode.

        return logger


def configure_toast_logging(level: int = logging.DEBUG) -> None:
    """
    Configure logging for standalone Toast execution.

    Args:
        level: Log level.
    """
    # Do not replace application logging.
    client_logger = logging.getLogger('client')
    server_logger = logging.getLogger('server')
    if client_logger.handlers or server_logger.handlers:
        return

    # Configure the root logger.
    handler = logging.StreamHandler(sys.stdout)
    formatter = LocalizedFormatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
