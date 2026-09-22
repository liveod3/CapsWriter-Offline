# coding: utf-8
"""
Client package.

Provide the CapsWriter client components.

Package layout:
- state: Client state.
- connection/: WebSocket connections.
- audio/: Recording, streams, and audio files.
- shortcut/: Recording shortcuts.
- output/: Result processing and output.
- udp/: UDP control.
- transcribe/: File transcription.
- diary/: Transcript archives.
- ui/: User interface.
"""

from config_client import ClientConfig as Config
from core.logger import get_logger, setup_logger

# Configure the main log level here.
setup_logger('client', level=Config.log_level)
logger = get_logger('client')

__all__ = [
    'CapsWriterClient',
]


def __getattr__(name):
    """Load the facade lazily so --help does not import hardware modules."""
    if name == 'CapsWriterClient':
        from core.client.app import CapsWriterClient
        return CapsWriterClient
    raise AttributeError(name)
