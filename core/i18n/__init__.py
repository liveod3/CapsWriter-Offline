"""Process-local UI language, independent of recognition and text content.

Catalogs are Python modules so source and frozen builds use the same resources.
Importing this module never imports configuration, audio, Tk or the client.
"""

from __future__ import annotations

import locale

from .en import MESSAGES as ENGLISH
from .zh_cn import MESSAGES as CHINESE

LANGUAGES = {'auto': 'language.auto', 'en': 'language.english', 'zh-CN': 'language.chinese'}
CATALOGS = {"en": ENGLISH, "zh-CN": CHINESE}
_language = "en"
_shared_language = None


def bind_shared_language(shared):
    """Read the server's published locale in spawned workers, without a watcher."""
    global _shared_language
    _shared_language = shared


def initialize_tool_language():
    """Standalone utilities follow the client preference, or the system locale."""
    try:
        from config_client import ClientConfig
    except ImportError:
        return set_language('auto')
    return set_language(getattr(ClientConfig, 'ui_language', 'auto'))


def system_language() -> str:
    """Use the Windows UI locale, with a portable locale fallback."""
    try:
        import ctypes

        code = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
    except (AttributeError, OSError):
        try:
            code = locale.getlocale()[0] or ""
        except (ValueError, TypeError):
            code = ""
    return "zh-CN" if code.replace("_", "-").lower() in {"zh-cn", "zh-sg", "zh-hans"} else "en"


def set_language(preference: str = "auto") -> str:
    """Publish one immutable locale name; unsupported startup values use English."""
    global _language
    _language = (
        system_language()
        if preference == "auto"
        else (preference if isinstance(preference, str) and preference in CATALOGS else "en")
    )
    return _language


def get_language() -> str:
    if _shared_language is not None:
        return 'zh-CN' if _shared_language.value == 1 else 'en'
    return _language


def tr(message_id: str, *, locale: str | None = None, **values) -> str:
    """Translate a stable ID, falling back to English and then the visible ID."""
    effective_locale = locale or get_language()
    template = CATALOGS.get(effective_locale, ENGLISH).get(message_id)
    if template is None:
        template = ENGLISH.get(message_id, message_id)
    if values:
        values = {key: localized_value(value, locale=effective_locale) for key, value in values.items()}
        return template.format(**values)
    return template


def lazy(message_id: str):
    """Resolve menu text when pystray builds a menu, rather than at startup."""
    return lambda _item: tr(message_id)


class Notice(str):
    """English diagnostic text carrying a separately localizable UI message."""

    def __new__(cls, message_id: str, **values):
        instance = super().__new__(cls, tr(message_id, locale="en", **values))
        instance.message_id = message_id
        instance.values = values
        return instance

    def localized(self) -> str:
        return tr(self.message_id, **self.values)

    def __reduce__(self):
        return (_restore_notice, (self.message_id, self.values))


def _restore_notice(message_id, values):
    return Notice(message_id, **values)


def localized_value(value, *, locale=None):
    """Render controlled nested notices without translating arbitrary content."""
    if isinstance(value, BaseException) and len(value.args) == 1 and isinstance(value.args[0], Notice):
        value = value.args[0]
    if isinstance(value, Notice):
        return tr(value.message_id, locale=locale, **value.values)
    return value


def localize_notice(message: str) -> str:
    return message.localized() if isinstance(message, Notice) else message
