"""Non-interactive tests for Kite authentication setup."""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from src.token_manager import TokenManager


def test_initialize_kite_uses_mocked_authentication(tmp_path):
    """Validate token-backed initialization without contacting Kite."""
    manager = TokenManager()
    manager.token_file = str(tmp_path / "kite_token.json")
    manager.access_token = "test-access-token"
    manager.token_expiry = datetime.now() + timedelta(hours=2)

    client = Mock()
    client.profile.return_value = {"user_name": "test-user"}
    with patch("src.token_manager.KiteConnect", return_value=client) as constructor:
        result = manager.initialize_kite()

    constructor.assert_called_once()
    client.set_access_token.assert_called_once_with("test-access-token")
    client.profile.assert_called_once_with()
    assert result is client
