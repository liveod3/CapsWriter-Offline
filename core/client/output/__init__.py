# coding: utf-8
"""
Output package.

Deliver recognition results to output targets.
"""

from .. import logger
from core.client.output.result_processor import ResultProcessor
from core.client.output.text_output import TextOutput

__all__ = [
    'logger',
    'ResultProcessor',
    'TextOutput',
]
