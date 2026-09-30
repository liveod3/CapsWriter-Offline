"""Deliver optional file-task metadata without coupling transcription to a UI."""

from __future__ import annotations


def notify_file_progress(app, event: dict) -> None:
    """Call a local observer; presentation failures cannot change a task's outcome.

    Callers provide only stages, timing, controlled failure IDs and saved output
    paths. Recognition text, audio and connection credentials never belong here.
    Observers must return promptly and marshal updates to their UI's owning thread.
    """
    callback = getattr(app, 'file_progress_callback', None)
    if callable(callback):
        try:
            callback(event)
        except Exception:
            # A disconnected observer must not interrupt output or owned cleanup.
            pass
