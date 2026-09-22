from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_ui_language(monkeypatch):
    """Tests explicitly select locales instead of inheriting the desktop language."""
    from core import i18n
    monkeypatch.setattr(i18n, '_language', 'en')
    monkeypatch.setattr(i18n, '_shared_language', None)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """按目录自动标记非默认测试，避免本地误触硬件或大模型。"""
    for item in items:
        parts = set(Path(str(item.path)).parts)
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        if "windows" in parts:
            item.add_marker(pytest.mark.windows)
