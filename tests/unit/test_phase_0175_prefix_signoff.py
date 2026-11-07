"""PHASE 0175: prefix area sign-off with store/hit/stats flow."""
from src.providers.base import ChatMessage, ProviderResponse
from src.orchestrator.prompt_cache import PromptCache


def test_prefix_area_signoff():
    cache = PromptCache()
    msgs = [ChatMessage(role="system", content="Be helpful."),
            ChatMessage(role="user", content="What is WinAI?")]
    cache.store(msgs, "mock", ProviderResponse(content="An AI environment.", model_name="mock"))
    hit = cache.get(msgs, "mock")
    assert hit is not None and hit[1] == "EXACT_CACHE_HIT"
    assert cache.get_stats()["exact_hits"] == 1
    aligned = PromptCache.align_prefix_caching("Rules.", [{"name": "t"}], msgs[1:])
    assert aligned[0].role == "system" and "Rules." in aligned[0].content
