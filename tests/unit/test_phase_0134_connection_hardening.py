"""PHASE 0134: connection summary fails closed on unknown providers."""
import pytest

from src.providers.local_adapter import LocalModelAdapter
from src.providers.registry import ProviderRegistry


def test_unknown_connection_raises_key_error():
    reg = ProviderRegistry()
    with pytest.raises(KeyError):
        reg.describe_connection("ghost")


def test_local_adapter_reports_local():
    reg = ProviderRegistry()
    reg.register_provider("local", LocalModelAdapter())
    info = reg.describe_connection("local")
    assert info["is_local"] is True
    assert info["provider_name"] == "local"
