"""PHASE 0023: registry membership helper without exception-driven flow."""
from src.providers.mock_provider import MockProvider
from src.providers.registry import ProviderRegistry


def test_has_provider_membership():
    reg = ProviderRegistry()
    assert reg.has_provider("mock") is True
    assert reg.has_provider("no_such_provider") is False
    reg.register_provider("extra", MockProvider())
    assert reg.has_provider("extra") is True
