"""PHASE 0174: expired entries miss and identical prompts hash equally."""
from src.providers.base import ChatMessage, ProviderResponse
from src.orchestrator.prompt_cache import PromptCache


def test_expired_entry_misses():
    cache = PromptCache()
    msgs = [ChatMessage(role="user", content="ttl probe question here")]
    cache.store(msgs, "m", ProviderResponse(content="r", model_name="m"), ttl_seconds=0.0)
    import time

    time.sleep(0.01)
    assert cache.get(msgs, "m") is None


def test_canonical_hash_stable_and_sensitive():
    h1 = PromptCache.compute_canonical_hash([ChatMessage(role="user", content=" hi ")], "m")
    h2 = PromptCache.compute_canonical_hash([ChatMessage(role="user", content="hi")], "m")
    h3 = PromptCache.compute_canonical_hash([ChatMessage(role="user", content="hi")], "other")
    assert h1 == h2
    assert h1 != h3
