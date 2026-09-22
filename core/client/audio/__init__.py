# coding: utf-8
"""
Audio package.

Provide recording, audio stream, and audio file management.
"""

from .. import logger
from core.client.audio.recorder import AudioRecorder
from core.client.audio.stream import AudioStreamManager
from core.client.audio.file_manager import AudioFileManager

__all__ = [
    'logger',
    'AudioRecorder',
    'AudioStreamManager',
    'AudioFileManager',
]
