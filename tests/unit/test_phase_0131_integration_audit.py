"""PHASE 0131: integration center contracts exist on both layers."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_connection_model_contract():
    text = (
        ROOT / "src" / "client" / "WinAI.Client" / "Models" / "ProviderConnectionModel.cs"
    ).read_text(encoding="utf-8")
    for token in ("EndpointUrl", "SelectedModel", "IsActive", "IsLocal", "HasCredentialStored"):
        assert token in text


def test_registry_lists_capabilities():
    from src.providers.registry import ProviderRegistry

    listed = ProviderRegistry().list_providers()
    assert any(p["id"] == "mock" for p in listed)
