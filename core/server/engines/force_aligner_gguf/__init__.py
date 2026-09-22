"""
Hybrid GGUF speech inference package.

Combine ONNX Runtime encoders with a llama.cpp GGUF decoder.

Expose a sherpa-onnx-compatible interface.
"""

import logging
import sys
import os

# Locate the application root in source and frozen layouts.
if getattr(sys, 'frozen', False):
    # Frozen layout: sys.executable is in the package root.
    ROOT_DIR = os.path.dirname(sys.executable)
else:
    # Source layout: resolve the root relative to this package.
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

default_log_file = os.path.join(ROOT_DIR, "logs", "latest.log")

def setup_logging(level: int = logging.WARNING, log_file: str = default_log_file):
    """
    Configure package logging.

    Args:
        level: DEBUG, INFO, WARNING, ERROR, or CRITICAL.
        log_file: Log filename.

    Returns:
        Configured logger.
    """
    # Get the root logger.
    root_logger = logging.getLogger('qwen_asr_gguf')
    root_logger.setLevel(level)  # Accept all log levels.
    root_logger.handlers.clear()  # Remove existing handlers.


    # File handler.
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(level) # Keep more detailed records in the file.
        file_formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    return root_logger


# Initialize default INFO logging.
try:
    from .. import logger
except:
    logger = setup_logging(level=logging.INFO)

