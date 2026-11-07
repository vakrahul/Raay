"""PHASE 0030: adapter area sign-off across the registry surface."""
from src.providers.anthropic_adapter import AnthropicAdapter
from src.providers.gemini_adapter import GeminiAdapter
from src.providers.local_adapter import LocalModelAdapter
from src.providers.mock_provider import MockProvider
from src.providers.openai_adapter import OpenAIAdapter
from src.providers.registry import ProviderRegistry


def test_adapter_area_signoff():
    reg = ProviderRegistry()
    reg.register_provider("openai", OpenAIAdapter(api_key="test"))
    reg.register_provider("anthropic", AnthropicAdapter(api_key="test"))
    reg.register_provider("gemini", GeminiAdapter(api_key="test"))
    reg.register_provider("local", LocalModelAdapter())
    for pid in ("mock", "openai", "anthropic", "gemini", "local"):
        caps = reg.get_provider(pid).get_capabilities()
        assert caps.context_window >= 1024
    assert len(reg.list_providers()) == 5
