"""PHASE 0025: provider contracts area sign-off via registry capabilities."""
from src.providers.registry import ProviderRegistry


def test_provider_area_signoff():
    reg = ProviderRegistry()
    assert reg.has_provider("mock")
    caps = reg.get_provider("mock").get_capabilities()
    assert caps.context_window >= 1024
    assert isinstance(caps.supports_streaming, bool)
    listed = reg.list_providers()
    assert any(p["id"] == "mock" and p["is_active"] for p in listed)
