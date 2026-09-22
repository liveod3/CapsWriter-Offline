import sys
import os
import logging

def setup_logging(level: int = logging.WARNING, log_file: str = os.path.join("logs", "latest.log")):
    """
    Configure package logging.

    Args:
        level: DEBUG, INFO, WARNING, ERROR, or CRITICAL.
        log_file: Log filename.

    Returns:
        Configured logger.
    """
    # Get the root logger.
    root_logger = logging.getLogger('fun_asr_gguf')
    root_logger.setLevel(logging.DEBUG)  # Accept all log levels.
    root_logger.handlers.clear()  # Remove existing handlers.

    # File handler.
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            
        file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG) # Keep more detailed records in the file.
        file_formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    return root_logger

try:
    from core import get_logger
    from core.server import console 
    logger = get_logger('server')
except:
    from rich.console import Console
    console = Console(highlight=False)
    logger = setup_logging(level=logging.INFO)

from .asr_engine import (
    SenseVoiceEngine,
)

from .inference.schema import (
    ASREngineConfig,
)

__all__ = [
    'logger',
    'SenseVoiceEngine',
    'ASREngineConfig',
]