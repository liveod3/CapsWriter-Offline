"""Edit local text-action settings without executing configuration or changing other values."""

from __future__ import annotations

from core.i18n import Notice

from pathlib import Path


def llm_options(config) -> dict[str, bool]:
    """Apply the master switch; legacy configurations allow both capabilities."""
    enabled = bool(getattr(config, "llm_enabled", False))
    return {
        "correct_asr": enabled and bool(getattr(config, "llm_correction_enabled", True)),
        "translate": enabled and bool(getattr(config, "llm_translation_enabled", True)),
    }


def save_llm_options(path: Path, *, correction: bool, translation: bool) -> None:
    """Save capability and master switches while preserving routing and other settings."""
    if not isinstance(correction, bool) or not isinstance(translation, bool):
        raise ValueError(Notice('validation.settings.llm_options_must_be_boolean'))
    _save_options(path, {
        "llm_enabled": correction or translation,
        "llm_correction_enabled": correction,
        "llm_translation_enabled": translation,
    })


def save_ui_language(path: Path, language: str) -> None:
    """Save the UI preference without executing or replacing other settings."""
    from core.i18n import LANGUAGES

    if language not in LANGUAGES:
        raise ValueError(Notice('validation.settings.unsupported_ui_language'))
    _save_options(path, {"ui_language": language})


def _save_options(path: Path, values: dict) -> None:
    """Compatibility helpers use the same validated transaction as all editors."""
    from core.settings import SettingsService
    from core.config_reload import CandidateError

    service = SettingsService.standalone(path)
    snapshot = service.read()
    if snapshot.error:
        raise CandidateError(snapshot.error)
    service.save(values, revision=snapshot.revision)
