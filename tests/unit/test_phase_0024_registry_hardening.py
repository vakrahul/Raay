"""PHASE 0024: registry rejects non-provider registrations fail-closed."""
import pytest

from src.providers.registry import ProviderRegistry


def test_registry_rejects_non_provider():
    reg = ProviderRegistry()
    with pytest.raises(TypeError):
        reg.register_provider("bogus", object())
    assert reg.has_provider("bogus") is False
