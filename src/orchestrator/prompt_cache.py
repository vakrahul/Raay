"""Exact and semantic response caching, duplicate detection, and prefix alignment (Module 1)."""
import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.orchestrator.brain.semantic_memory import cosine_similarity, simple_embedding
from src.providers.base import ChatMessage, ProviderResponse


class CachedResponse(BaseModel):
    canonical_hash: str
    response: ProviderResponse
    model_name: str
    prompt_sample: str
    timestamp: float = Field(default_factory=time.time)
    ttl_seconds: float = 3600.0  # 1 hour default
    hit_count: int = 0
    embedding: Optional[List[float]] = None

    @property
    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl_seconds


class PromptCache:
    """Multi-tiered response cache: Exact SHA-256 deduplication and Semantic similarity."""

    def __init__(self, semantic_threshold: float = 0.95):
        self._exact_cache: Dict[str, CachedResponse] = {}
        self.semantic_threshold = semantic_threshold
        self.total_exact_hits = 0
        self.total_semantic_hits = 0
        self.total_tokens_saved = 0

    @staticmethod
    def compute_canonical_hash(messages: List[ChatMessage], model_name: str) -> str:
        """Compute deterministic SHA-256 hash of prompt sequence and model."""
        canonical_repr = json.dumps({
            "model": model_name,
            "messages": [{"role": m.role, "content": m.content.strip()} for m in messages],
        }, sort_keys=True)
        return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()

    def get(self, messages: List[ChatMessage], model_name: str) -> Optional[Tuple[ProviderResponse, str]]:
        """Look up response in exact cache first, then semantic cache.

        Returns (ProviderResponse, hit_type) or None if miss.
        """
        canonical_hash = self.compute_canonical_hash(messages, model_name)

        # 1. Exact Cache Check
        if canonical_hash in self._exact_cache:
            entry = self._exact_cache[canonical_hash]
            if not entry.is_expired:
                entry.hit_count += 1
                self.total_exact_hits += 1
                # Estimate tokens saved
                saved = len(entry.response.content) // 4
                self.total_tokens_saved += saved
                return entry.response, "EXACT_CACHE_HIT"
            else:
                del self._exact_cache[canonical_hash]

        # 2. Semantic Cache Check (for single user question / informational prompt)
        last_user_msg = next((m.content for m in reversed(messages) if m.role == "user"), None)
        if last_user_msg and len(last_user_msg) > 10:
            query_emb = simple_embedding(last_user_msg)
            for entry in self._exact_cache.values():
                if entry.model_name == model_name and entry.embedding and not entry.is_expired:
                    sim = cosine_similarity(query_emb, entry.embedding)
                    if sim >= self.semantic_threshold:
                        entry.hit_count += 1
                        self.total_semantic_hits += 1
                        saved = len(entry.response.content) // 4
                        self.total_tokens_saved += saved
                        return entry.response, f"SEMANTIC_CACHE_HIT (score: {sim:.2f})"

        return None

    def store(
        self,
        messages: List[ChatMessage],
        model_name: str,
        response: ProviderResponse,
        ttl_seconds: float = 3600.0,
    ) -> None:
        """Store response in cache and index embedding for semantic retrieval."""
        canonical_hash = self.compute_canonical_hash(messages, model_name)
        last_user_msg = next((m.content for m in reversed(messages) if m.role == "user"), "")
        emb = simple_embedding(last_user_msg) if last_user_msg else None

        self._exact_cache[canonical_hash] = CachedResponse(
            canonical_hash=canonical_hash,
            response=response,
            model_name=model_name,
            prompt_sample=last_user_msg[:120],
            ttl_seconds=ttl_seconds,
            embedding=emb,
        )

    def invalidate_model(self, model_name: str) -> int:
        """Purge all cached entries for a model; returns removed count."""
        doomed = [
            key for key, entry in self._exact_cache.items()
            if entry.model_name == model_name
        ]
        for key in doomed:
            del self._exact_cache[key]
        return len(doomed)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "cached_entries_count": len(self._exact_cache),
            "exact_hits": self.total_exact_hits,
            "semantic_hits": self.total_semantic_hits,
            "total_hits": self.total_exact_hits + self.total_semantic_hits,
            "estimated_tokens_saved": self.total_tokens_saved,
        }

    @staticmethod
    def align_prefix_caching(
        system_instructions: str,
        tool_schemas: List[Dict[str, Any]],
        conversation_history: List[ChatMessage],
    ) -> List[ChatMessage]:
        """Align prompt so invariant prefixes appear first, maximizing provider-level prompt caching."""
        # Consolidate static invariants into the head system message
        static_block = system_instructions.strip()
        if tool_schemas:
            schema_json = json.dumps(tool_schemas, sort_keys=True)
            static_block += f"\n\n[INVARIANT_TOOL_SCHEMAS]\n{schema_json}"

        aligned_messages = [
            ChatMessage(role="system", content=static_block)
        ] + [m for m in conversation_history if m.role != "system"]

        return aligned_messages
