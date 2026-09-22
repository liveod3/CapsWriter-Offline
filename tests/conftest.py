from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_ui_language(monkeypatch):
    """Tests explicitly select locales instead of inheriting the desktop language."""
    from core import i18n
    monkeypatch.setattr(i18n, '_language', 'en')
    monkeypatch.setattr(i18n, '_shared_language', None)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark non-default tests by directory to avoid accidental hardware/model execution."""
    for item in items:
        parts = set(Path(str(item.path)).parts)
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        if "windows" in parts:
            item.add_marker(pytest.mark.windows)
