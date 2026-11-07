"""Mock JEV decision adapter for offline testing ONLY.

WARNING: This is a deterministic heuristic simulator. It is NOT the real
TypeSafe AI Jev model, it performs no calibrated inference, and its outputs
must never be presented as real JEV decisions. Production use requires a
verified provider implementing BaseJevProvider with is_mock=False.
"""

import asyncio
import hashlib
import re
import time
from typing import Dict, List

from src.orchestrator.jev.base import (
    BaseJevProvider,
    JevDecisionKind,
    JevDecisionRequest,
    JevDecisionResponse,
)

_COMPLEX_HINTS = (
    "refactor", "architecture", "debug", "deploy", "migrate", "security",
    "optimize", "distributed", "concurrency", "kernel",
)
_CAPABLE_LABEL_HINTS = ("pro", "opus", "gpt-4", "claude", "frontier", "max", "ultra")
_CHEAP_LABEL_HINTS = ("flash", "lite", "mini", "haiku", "local", "mock", "nano")
_THREAT_MARKERS = (
    "ignore previous instructions", "ignore all prior instructions",
    "[system:", "<script", "rm -rf", "powershell -enc", "sudo",
    "unrestricted mode", "developer mode",
)
_THREAT_LABEL_HINTS = ("unsafe", "block", "deny", "malicious", "threat", "inject", "risky")
_SAFE_LABEL_HINTS = ("safe", "allow", "clean", "benign")


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _tiebreak(label: str) -> float:
    digest = hashlib.md5(label.encode("utf-8")).digest()
    return (int.from_bytes(digest[:4], "little") % 1000) / 100000.0


def _score_candidates(kind: JevDecisionKind, context: str, candidates: List[str]) -> Dict[str, float]:
    ctx = context.lower()
    ctx_tokens = set(_tokens(context))
    scores: Dict[str, float] = {}
    for cand in candidates:
        overlap = len(set(_tokens(cand)) & ctx_tokens)
        score = float(overlap) + _tiebreak(cand)
        lowered = cand.lower()
        if kind == JevDecisionKind.GUARDRAIL_ASSESSMENT:
            if any(m in ctx for m in _THREAT_MARKERS):
                if any(h in lowered for h in _THREAT_LABEL_HINTS):
                    score += 5.0
            else:
                if any(h in lowered for h in _SAFE_LABEL_HINTS):
                    score += 3.0
        elif kind == JevDecisionKind.TASK_CLASSIFICATION:
            complexity = sum(1 for h in _COMPLEX_HINTS if h in ctx)
            if complexity >= 2:
                if any(h in lowered for h in ("complex", "high", "advanced", "deep")):
                    score += 4.0
            elif complexity == 0 and len(ctx_tokens) < 12:
                if any(h in lowered for h in ("simple", "low", "basic", "trivial", "easy")):
                    score += 4.0
            else:
                if any(h in lowered for h in ("moderate", "medium", "mid")):
                    score += 4.0
        elif kind == JevDecisionKind.MODEL_SELECTION:
            complexity = sum(1 for h in _COMPLEX_HINTS if h in ctx)
            if complexity >= 2:
                if any(h in lowered for h in _CAPABLE_LABEL_HINTS):
                    score += 4.0
            elif complexity == 0 and len(ctx_tokens) < 12:
                if any(h in lowered for h in _CHEAP_LABEL_HINTS):
                    score += 4.0
        scores[cand] = score + 1.0  # Laplace smoothing keeps every option possible
    total = sum(scores.values())
    return {cand: score / total for cand, score in scores.items()}


class MockJevAdapter(BaseJevProvider):
    """Deterministic offline stand-in. is_mock=True always."""

    provider_name = "mock-jev"
    is_mock = True

    def __init__(self, simulated_latency_ms: float = 5.0):
        self.simulated_latency_ms = simulated_latency_ms

    async def health_check(self) -> bool:
        return True

    async def decide(self, request: JevDecisionRequest) -> JevDecisionResponse:
        started = time.perf_counter()
        if self.simulated_latency_ms > 0:
            await asyncio.sleep(self.simulated_latency_ms / 1000.0)
        probabilities = _score_candidates(request.kind, request.prompt_context, request.candidates)
        selected = max(probabilities, key=lambda c: probabilities[c])
        latency_ms = (time.perf_counter() - started) * 1000.0
        return JevDecisionResponse(
            request_id=request.request_id,
            kind=request.kind,
            selected=selected,
            probabilities=probabilities,
            confidence=probabilities[selected],
            latency_ms=latency_ms,
            provider_name=self.provider_name,
            is_mock=True,
        )
