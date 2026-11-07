"""PHASE 0028: registry ID inventory helper."""
from src.providers.mock_provider import MockProvider
from src.providers.registry import ProviderRegistry


def test_registered_ids_sorted_and_current():
    reg = ProviderRegistry()
    assert reg.registered_ids() == ["mock"]
    reg.register_provider("zeta", MockProvider())
    reg.register_provider("alpha", MockProvider())
    assert reg.registered_ids() == ["alpha", "mock", "zeta"]
