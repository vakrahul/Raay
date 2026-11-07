"""PHASE 0021: provider contracts and registry behavior audit."""
import inspect

from src.providers.base import BaseModelProvider
from src.providers.registry import ProviderRegistry


def test_base_contract_declares_four_abstract_members():
    abstract = set(getattr(BaseModelProvider, "__abstractmethods__", set()))
    assert {"complete", "stream", "get_capabilities", "health_check"} <= abstract


def test_registry_defaults_to_mock_and_rejects_unknown():
    reg = ProviderRegistry()
    assert reg.get_provider().get_capabilities().provider_name == "mock"
    try:
        reg.get_provider("no_such_provider")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    assert inspect.isclass(ProviderRegistry)
