"""Semantic Memory: Vector retrieval and conceptual similarity search."""
import hashlib
import math
import re
from typing import Dict, List, Optional, Tuple
from src.orchestrator.brain.models import MemoryEntry, MemoryType, VerificationStatus


def _stable_token_index(token: str, dimensions: int) -> int:
    """Map a token to a bucket deterministically across processes.

    NOTE: Python's builtin hash() is salted per process, so it must never
    be used here; MD5 keeps embeddings stable between restarts.
    """
    digest = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") % dimensions


def simple_embedding(text: str, dimensions: int = 64) -> List[float]:
    """Deterministic, lightweight local embedding projection for semantic matching."""
    tokens = re.findall(r"\w+", text.lower())
    vec = [0.0] * dimensions
    if not tokens:
        return vec

    for token in tokens:
        idx = _stable_token_index(token, dimensions)
        vec[idx] += 1.0

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Compute cosine similarity between two unit or real vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticMemory:
    """Manages semantic facts and vector similarity queries."""

    def __init__(self):
        self._entries: Dict[str, MemoryEntry] = {}

    def store_fact(
        self,
        fact_id: str,
        content: str,
        tags: Optional[List[str]] = None,
        verification_status: VerificationStatus = VerificationStatus.USER_ASSERTED,
    ) -> MemoryEntry:
        emb = simple_embedding(content)
        entry = MemoryEntry(
            id=fact_id,
            memory_type=MemoryType.SEMANTIC,
            content=content,
            verification_status=verification_status,
            tags=tags or [],
            embedding=emb,
        )
        self._entries[fact_id] = entry
        return entry

    def retrieve_similar(self, query: str, top_k: int = 5, min_score: float = 0.2) -> List[Tuple[MemoryEntry, float]]:
        """Find the top-K most semantically relevant memories."""
        query_emb = simple_embedding(query)
        scored: List[Tuple[MemoryEntry, float]] = []

        for entry in self._entries.values():
            if entry.embedding:
                score = cosine_similarity(query_emb, entry.embedding)
                if score >= min_score:
                    scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def delete_fact(self, fact_id: str) -> bool:
        return self._entries.pop(fact_id, None) is not None

    def get_all(self) -> List[MemoryEntry]:
        return list(self._entries.values())
