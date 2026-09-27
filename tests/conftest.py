from pathlib import Path

import pytest


@pytest.fixture
def attach_client_operations():
    """Attach real settings operations to synthetic applications without hardware."""
    def attach(app, config):
        from types import SimpleNamespace
        from config_templates import config_client_template
        from core.client.operations import ClientOperations
        from core.config_reload import ConfigReloader, CLIENT_LIVE, read_settings

        path = app.base_dir / 'config_client.py'
        app.config_reload = ConfigReloader(
            path, SimpleNamespace(ClientConfig=config), config_client_template,
            'ClientConfig', CLIENT_LIVE, lambda _: None,
        )
        parsed = read_settings(path.read_bytes(), path)
        app.config_reload.required = {'ClientConfig': set(parsed['ClientConfig'])}
        app.operations = ClientOperations(app)
        return app.operations
    return attach


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
