"""PHASE 0156: every adapter exposes an async streaming generator."""
import inspect

from src.providers.anthropic_adapter import AnthropicAdapter
from src.providers.base import BaseModelProvider
from src.providers.gemini_adapter import GeminiAdapter
from src.providers.local_adapter import LocalModelAdapter
from src.providers.mock_provider import MockProvider
from src.providers.openai_adapter import OpenAIAdapter


def test_all_adapters_define_async_stream():
    assert "stream" in BaseModelProvider.__abstractmethods__
    for cls in (MockProvider, OpenAIAdapter, AnthropicAdapter, GeminiAdapter, LocalModelAdapter):
        assert inspect.isasyncgenfunction(cls.stream)
