"""PHASE 0171: prefix cache primitives exist with TTL and stats."""
from src.orchestrator.prompt_cache import PromptCache


def test_prefix_cache_primitives_present():
    cache = PromptCache()
    for method in ("compute_canonical_hash", "get", "store", "get_stats", "align_prefix_caching"):
        assert hasattr(cache, method) or hasattr(PromptCache, method)
    assert cache.semantic_threshold == 0.95
