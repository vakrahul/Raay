"""Provider Registry and dynamic lifecycle manager."""
from typing import Any, Dict, List, Optional
from src.providers.base import BaseModelProvider, ModelCapabilities
from src.providers.mock_provider import MockProvider
from src.providers.openai_adapter import OpenAIAdapter
from src.providers.anthropic_adapter import AnthropicAdapter
from src.providers.gemini_adapter import GeminiAdapter
from src.providers.local_adapter import LocalModelAdapter
from src.storage.credential_vault import CredentialVault


class ProviderRegistry:
    """Central registry managing multiple LLM providers and dynamic switching."""

    def __init__(self, vault: Optional[CredentialVault] = None):
        self.vault = vault or CredentialVault()
        self._providers: Dict[str, BaseModelProvider] = {}
        self._active_provider_id = "mock"

        # Register default mock provider
        self.register_provider("mock", MockProvider())

    def register_provider(self, provider_id: str, provider: BaseModelProvider) -> None:
        """Register a new provider adapter instance."""
        if not isinstance(provider, BaseModelProvider):
            raise TypeError(
                f"Refused to register '{provider_id}': not a BaseModelProvider."
            )
        self._providers[provider_id] = provider

    def has_provider(self, provider_id: str) -> bool:
        """Return True when the provider ID is registered (no secrets exposed)."""
        return provider_id in self._providers

    def registered_ids(self) -> list:
        """Return sorted registered provider IDs (capability metadata only)."""
        return sorted(self._providers)

    def describe_connection(self, provider_id: str) -> Dict[str, Any]:
        """Return synchronous connection metadata for the integration center.

        Reports identity and capability flags only; health probing stays
        asynchronous and no secret material is included.
        """
        adapter = self.get_provider(provider_id)
        caps = adapter.get_capabilities()
        return {
            "id": provider_id,
            "provider_name": caps.provider_name,
            "model_name": caps.model_name,
            "is_active": provider_id == self._active_provider_id,
            "is_local": caps.provider_name == "local",
            "supports_streaming": caps.supports_streaming,
            "supports_tools": caps.supports_tools,
        }

    def get_provider(self, provider_id: Optional[str] = None) -> BaseModelProvider:
        """Retrieve provider adapter by ID or return the active provider."""
        pid = provider_id or self._active_provider_id
        if pid not in self._providers:
            raise KeyError(f"Provider '{pid}' is not registered.")
        return self._providers[pid]

    def set_active_provider(self, provider_id: str) -> None:
        """Set default active provider for new tasks."""
        if provider_id not in self._providers:
            raise KeyError(f"Cannot set unknown provider '{provider_id}' as active.")
        self._active_provider_id = provider_id

    def list_providers(self) -> List[Dict[str, Any]]:
        """List all registered providers and their declared capabilities."""
        results = []
        for pid, adapter in self._providers.items():
            caps = adapter.get_capabilities()
            results.append({
                "id": pid,
                "is_active": (pid == self._active_provider_id),
                "provider_name": caps.provider_name,
                "model_name": caps.model_name,
                "context_window": caps.context_window,
                "supports_tools": caps.supports_tools,
                "supports_streaming": caps.supports_streaming,
            })
        return results

    def initialize_from_vault(self) -> None:
        """Instantiate official adapters using encrypted keys from the credential vault."""
        # OpenAI
        openai_key = self.vault.get_credential("openai")
        if openai_key:
            self.register_provider("openai", OpenAIAdapter(api_key=openai_key))

        # Anthropic
        anthropic_key = self.vault.get_credential("anthropic")
        if anthropic_key:
            self.register_provider("anthropic", AnthropicAdapter(api_key=anthropic_key))

        # Gemini
        gemini_key = self.vault.get_credential("gemini")
        if gemini_key:
            self.register_provider("gemini", GeminiAdapter(api_key=gemini_key))

        # Local Ollama
        self.register_provider("local", LocalModelAdapter())
