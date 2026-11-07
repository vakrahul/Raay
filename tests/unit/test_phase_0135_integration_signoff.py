"""PHASE 0135: integration area sign-off with vault-backed registry."""
from src.providers.registry import ProviderRegistry
from src.storage.credential_vault import CredentialVault


def test_integration_area_signoff(tmp_path):
    vault = CredentialVault(vault_path=tmp_path / "vault.enc")
    reg = ProviderRegistry(vault=vault)
    reg.initialize_from_vault()
    assert reg.has_provider("mock")
    assert reg.has_provider("local")
    info = reg.describe_connection("mock")
    assert set(info) == {
        "id", "provider_name", "model_name", "is_active",
        "is_local", "supports_streaming", "supports_tools",
    }
