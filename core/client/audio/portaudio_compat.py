"""Isolate sounddevice's private device-cache refresh behind a version guard.

Call only with the audio lifecycle lock held and no open input stream.
The 0.5 API is covered by mocks; Windows device switching still needs manual QA.
"""

from core.i18n import Notice

from . import logger


def refresh_devices(backend) -> bool:
    version = str(getattr(backend, '__version__', ''))
    if not version.startswith('0.5.'):
        logger.warning(Notice('diagnostic.portaudio_compat.device_refresh_unavailable_for_sounddevice_version'), version)
        return False
    terminate = getattr(backend, '_terminate', None)
    initialize = getattr(backend, '_initialize', None)
    if not callable(terminate) or not callable(initialize):
        logger.warning(Notice('diagnostic.portaudio_compat.device_refresh_api_unavailable'))
        return False
    initialized = getattr(backend, '_initialized', None)
    if type(initialized) is not int or initialized not in (0, 1):
        logger.warning(Notice('diagnostic.portaudio_compat.device_refresh_skipped_for_unknown_or_shared_initialization'))
        return False
    try:
        # A failed initialize leaves the count at zero. Do not terminate again
        # on retry: sounddevice also uses this count during interpreter exit.
        if initialized:
            terminate()
        initialize()
        return True
    except Exception as exc:
        logger.warning(Notice('diagnostic.portaudio_compat.device_refresh_failed'), type(exc).__name__)
        return False
