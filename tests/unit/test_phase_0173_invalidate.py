"""PHASE 0173: model invalidation purges only matching entries."""
from src.providers.base import ChatMessage, ProviderResponse
from src.orchestrator.prompt_cache import PromptCache


def _store(cache, text, model):
    msgs = [ChatMessage(role="user", content=text)]
    cache.store(msgs, model, ProviderResponse(content="r", model_name=model))


def test_invalidate_model_scoped():
    cache = PromptCache()
    _store(cache, "q one here", "model-a")
    _store(cache, "q two here", "model-b")
    assert cache.invalidate_model("model-a") == 1
    assert cache.invalidate_model("model-a") == 0
    assert cache.get_stats()["cached_entries_count"] == 1
