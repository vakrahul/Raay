"""Unit tests for Stage III: Multi-Provider AI Integration & Credential Vault."""
import asyncio
from pathlib import Path
import pytest

from src.providers.base import ChatMessage
from src.providers.mock_provider import MockProvider
from src.providers.openai_adapter import OpenAIAdapter
from src.providers.anthropic_adapter import AnthropicAdapter
from src.providers.gemini_adapter import GeminiAdapter
from src.providers.local_adapter import LocalModelAdapter
from src.providers.registry import ProviderRegistry
from src.providers.resilience import CircuitBreaker, CircuitState, retry_with_backoff
from src.storage.credential_vault import CredentialVault


@pytest.mark.unit
def test_provider_registry_and_capabilities():
    """Verify provider registration, active switching, and capability negotiation."""
    registry = ProviderRegistry()
    
    # Check default mock
    mock_prov = registry.get_provider()
    caps = mock_prov.get_capabilities()
    assert caps.provider_name == "mock"
    assert caps.supports_streaming is True

    # Register local adapter
    local_prov = LocalModelAdapter(base_url="http://127.0.0.1:11434", model_name="llama3.2")
    registry.register_provider("ollama_local", local_prov)
    
    # Switch active
    registry.set_active_provider("ollama_local")
    active = registry.get_provider()
    assert active.get_capabilities().model_name == "llama3.2"

    # List providers
    prov_list = registry.list_providers()
    assert len(prov_list) == 2
    assert any(p["id"] == "ollama_local" and p["is_active"] for p in prov_list)


@pytest.mark.unit
def test_all_adapters_capability_contracts():
    """Verify all provider adapters declare capabilities properly conforming to base contract."""
    openai_adp = OpenAIAdapter(api_key="test_key", model_name="gpt-4o")
    assert openai_adp.get_capabilities().context_window == 128000
    assert openai_adp.get_capabilities().supports_tools is True

    anthropic_adp = AnthropicAdapter(api_key="test_key", model_name="claude-3-5-sonnet")
    assert anthropic_adp.get_capabilities().context_window == 200000
    assert anthropic_adp.get_capabilities().supports_vision is True

    gemini_adp = GeminiAdapter(api_key="test_key", model_name="gemini-2.0-flash")
    assert gemini_adp.get_capabilities().context_window == 1000000

    local_adp = LocalModelAdapter(model_name="mistral:latest")
    assert local_adp.get_capabilities().provider_name == "local"


@pytest.mark.unit
def test_circuit_breaker_state_transitions():
    """Verify circuit breaker opens after repeated failures and blocks requests."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True

    # Record 3 failures
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False


@pytest.mark.asyncio
async def test_retry_with_backoff_transient_recovery():
    """Verify retry helper retries transient exceptions and returns successful result."""
    attempts = 0

    async def flaky_service():
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise ConnectionError("Temporary connection glitch")
        return "SUCCESS"

    result = await retry_with_backoff(flaky_service, max_retries=3, initial_delay=0.01)
    assert result == "SUCCESS"
    assert attempts == 2


@pytest.mark.unit
def test_credential_vault_dpapi_cycle(temp_workspace: Path):
    """Verify hardware-backed DPAPI credential storage encrypts, retrieves, and deletes keys."""
    vault_file = temp_workspace / "vault.enc"
    vault = CredentialVault(vault_path=vault_file)

    # Store credentials
    vault.store_credential("openai", "sk-proj-testkey1234567890abcdef")
    vault.store_credential("anthropic", "sk-ant-testkey0987654321")

    # Inspect file is not plaintext
    raw_content = vault_file.read_bytes()
    assert b"sk-proj-testkey" not in raw_content
    assert b"sk-ant-testkey" not in raw_content

    # Retrieve credentials
    assert vault.get_credential("openai") == "sk-proj-testkey1234567890abcdef"
    assert vault.get_credential("anthropic") == "sk-ant-testkey0987654321"
    assert vault.get_credential("unknown") is None

    # List configured
    configured = vault.list_configured_providers()
    assert "openai" in configured
    assert "anthropic" in configured

    # Delete
    deleted = vault.delete_credential("openai")
    assert deleted is True
    assert vault.get_credential("openai") is None
