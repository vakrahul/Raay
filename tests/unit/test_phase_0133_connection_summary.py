"""PHASE 0133: synchronous connection metadata without secrets or I/O."""
from src.providers.mock_provider import MockProvider
from src.providers.registry import ProviderRegistry


def test_describe_connection_metadata():
    reg = ProviderRegistry()
    info = reg.describe_connection("mock")
    assert info["id"] == "mock"
    assert info["is_active"] is True
    assert "api_key" not in str(info).lower()
    reg.register_provider("second", MockProvider())
    assert reg.describe_connection("second")["is_active"] is False
