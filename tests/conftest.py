from pathlib import Path

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """按目录自动标记非默认测试，避免本地误触硬件或大模型。"""
    for item in items:
        parts = set(Path(str(item.path)).parts)
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        if "windows" in parts:
            item.add_marker(pytest.mark.windows)
