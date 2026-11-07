"""JEV cost and performance management (Stage 8).

Tracks decision requests, latency, success/failure, availability, cost,
and fallback frequency per provider. Enforces request budgets and
activation policy so no unnecessary external call is ever made. All
figures are measured, never fabricated: costs derive from the published
Jev tariff ($0.042/M input tokens, $0.00 output) applied to counted
characters.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from src.orchestrator.jev.base import JevDecisionKind

# Published Jev tariff (TypeSafe AI announcement, Sep 2026).
JEV_INPUT_USD_PER_MILLION_TOKENS = 0.042
JEV_OUTPUT_USD_PER_MILLION_TOKENS = 0.0


def estimate_jev_cost_usd(prompt_chars: int) -> float:
    """Estimate one decision's cost from counted characters (~4 chars/token)."""
    tokens = max(0, prompt_chars) / 4.0
    return tokens / 1_000_000.0 * JEV_INPUT_USD_PER_MILLION_TOKENS


@dataclass
class ProviderUsage:
    requests: int = 0
    successes: int = 0
    failures: int = 0
    timeouts: int = 0
    fallbacks: int = 0
    validation_failures: int = 0
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.requests if self.requests else 0.0

    @property
    def fallback_rate(self) -> float:
        return self.fallbacks / self.requests if self.requests else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.requests if self.requests else 0.0


class JevUsageTracker:
    """Per-provider usage, availability, cost, and fallback accounting."""

    def __init__(self):
        self._usage: Dict[str, ProviderUsage] = {}

    def _entry(self, provider_name: str) -> ProviderUsage:
        return self._usage.setdefault(provider_name, ProviderUsage())

    def record_success(self, provider_name: str, latency_ms: float, prompt_chars: int) -> None:
        entry = self._entry(provider_name)
        entry.requests += 1
        entry.successes += 1
        entry.total_latency_ms += latency_ms
        entry.total_cost_usd += estimate_jev_cost_usd(prompt_chars)

    def record_failure(self, provider_name: str, timed_out: bool = False) -> None:
        entry = self._entry(provider_name)
        entry.requests += 1
        entry.failures += 1
        if timed_out:
            entry.timeouts += 1

    def record_fallback(self, provider_name: str) -> None:
        self._entry(provider_name).fallbacks += 1

    def record_validation_failure(self, provider_name: str) -> None:
        entry = self._entry(provider_name)
        entry.requests += 1
        entry.failures += 1
        entry.validation_failures += 1

    def record_override(self, provider_name: str) -> None:
        """Record a human override of a JEV recommendation."""
        self._entry(provider_name).fallbacks += 1

    def summary(self, provider_name: str) -> Dict[str, float]:
        entry = self._entry(provider_name)
        return {
            "requests": entry.requests,
            "success_rate": entry.success_rate,
            "fallback_rate": entry.fallback_rate,
            "avg_latency_ms": entry.avg_latency_ms,
            "total_cost_usd": entry.total_cost_usd,
            "timeouts": entry.timeouts,
            "validation_failures": entry.validation_failures,
        }

    def providers(self) -> List[str]:
        return sorted(self._usage)


class JevRequestBudget:
    """Bounds JEV calls per scope (e.g., one task). Exhaustion forces fallback."""

    def __init__(self, max_decisions: int):
        if max_decisions < 0:
            raise ValueError("max_decisions must be non-negative")
        self.max_decisions = max_decisions
        self.consumed = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_decisions - self.consumed)

    def try_consume(self) -> bool:
        """Consume one decision slot. False means: do not call any provider."""
        if self.consumed >= self.max_decisions:
            return False
        self.consumed += 1
        return True


class JevActivationPolicy(BaseModel):
    """Configurable gates deciding whether JEV may be consulted at all."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    allowed_kinds: Set[JevDecisionKind] = Field(
        default_factory=lambda: set(JevDecisionKind)
    )

    def should_activate(self, kind: JevDecisionKind) -> bool:
        return self.enabled and kind in self.allowed_kinds
