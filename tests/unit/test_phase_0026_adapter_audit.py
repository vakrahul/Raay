"""PHASE 0026: adapter inventory — all adapters honor the base contract."""
import inspect

from src.providers import base as base_mod
from src.providers import registry as registry_mod
from src.providers.anthropic_adapter import AnthropicAdapter
from src.providers.base import BaseModelProvider
from src.providers.gemini_adapter import GeminiAdapter
from src.providers.local_adapter import LocalModelAdapter
from src.providers.mock_provider import MockProvider
from src.providers.openai_adapter import OpenAIAdapter


def test_all_adapters_subclass_base_and_define_health_check():
    for cls in (MockProvider, OpenAIAdapter, AnthropicAdapter, GeminiAdapter, LocalModelAdapter):
        assert issubclass(cls, BaseModelProvider)
        assert inspect.iscoroutinefunction(cls.health_check)


def test_resilience_primitives_present():
    import src.providers.resilience as resilience

    assert hasattr(resilience, "CircuitBreaker")
    assert hasattr(resilience, "CircuitBreakerOpenError")
    assert "mock_provider" in dir(registry_mod) or hasattr(registry_mod, "ProviderRegistry")
    assert hasattr(base_mod, "ProviderResponse")
