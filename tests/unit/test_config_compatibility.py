from unittest.mock import patch

from config_server import ServerConfig
from core.server.connection.ws_recv import _positive_limit


def test_server_limit_accepts_legacy_string_values() -> None:
    with patch.object(ServerConfig, "test_limit", "7", create=True):
        assert _positive_limit("test_limit", 5) == 7


def test_server_limit_uses_safe_default_for_invalid_or_non_positive_values() -> None:
    with patch.object(ServerConfig, "test_limit", "invalid", create=True):
        assert _positive_limit("test_limit", 5) == 5
    with patch.object(ServerConfig, "test_limit", 0, create=True):
        assert _positive_limit("test_limit", 5) == 5
    assert _positive_limit("missing_test_limit", 5) == 5
