# coding: utf-8
"""
Transcription package.

Provide file transcription.
"""

from .. import logger
from core.client.transcribe.file_transcriber import FileTranscriber
from core.client.transcribe.srt_adjuster import SrtAdjuster

__all__ = [
    'logger',
    'FileTranscriber',
    'SrtAdjuster',
]
