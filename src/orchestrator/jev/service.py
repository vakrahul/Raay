"""JEV runtime service for UI integration (Stage 9).

Single in-process owner of the JEV stack: provider, router, usage
tracker, decision log, request budget, and activation policy. The
service never blocks the UI: every decision path is bounded by
timeouts and budgets, and disabling the service stops all provider
traffic immediately while the rest of the application keeps working.
"""

from typing import Any, Dict, List, Optional

from src.orchestrator.jev.base import JevDecisionKind
from src.orchestrator.jev.decision_context import JevDecisionLog
from src.orchestrator.jev.mock_adapter import MockJevAdapter
from src.orchestrator.jev.router import JevDecisionRouter, JevRouterConfig
from src.orchestrator.jev.usage_tracker import (
    JevActivationPolicy,
    JevRequestBudget,
    JevUsageTracker,
)


class JevService:
    """Owns JEV configuration, status, metrics, and bounded test decisions."""

    def __init__(
        self,
        provider: Optional[object] = None,
        budget_max_decisions: int = 10000,
        confidence_threshold: float = 0.55,
        timeout_ms: int = 500,
    ):
        self.provider = provider or MockJevAdapter()
        self.router = JevDecisionRouter(
            self.provider, JevRouterConfig(confidence_threshold=confidence_threshold, timeout_ms=timeout_ms)
        )
        self.tracker = JevUsageTracker()
        self.decision_log = JevDecisionLog()
        self.budget = JevRequestBudget(max_decisions=budget_max_decisions)
        self.activation = JevActivationPolicy()
        self.timeout_ms = timeout_ms

    # --- Configuration ---

    def set_enabled(self, enabled: bool) -> bool:
        self.activation.enabled = bool(enabled)
        return self.activation.enabled

    @property
    def enabled(self) -> bool:
        return self.activation.enabled

    # --- Status ---

    async def status_dict(self) -> Dict[str, Any]:
        try:
            healthy = bool(await self.provider.health_check())
        except Exception:
            healthy = False
        return {
            "enabled": self.enabled,
            "provider_name": self.provider.provider_name,
            "is_mock": bool(self.provider.is_mock),
            "mock_warning": (
                "MOCK MODE — simulated decisions, not real JEV output."
                if self.provider.is_mock
                else None
            ),
            "healthy": healthy,
            "capabilities": [k.value for k in JevDecisionKind],
            "verification": "mock-unverified" if self.provider.is_mock else "provider-supplied",
            "budget_remaining": self.budget.remaining,
            "timeout_ms": self.timeout_ms,
        }

    def metrics_dict(self) -> Dict[str, Any]:
        return {
            "usage": {name: self.tracker.summary(name) for name in self.tracker.providers()},
            "router": {
                use_case: {
                    "requests": m.requests,
                    "jev_selected": m.jev_selected,
                    "fallbacks": m.fallbacks,
                    "timeouts": m.timeouts,
                    "avg_latency_ms": m.avg_latency_ms,
                }
                for use_case, m in self.router.metrics().items()
            },
        }

    def recent_decisions(self, limit: int = 10) -> List[Dict[str, Any]]:
        return [
            {
                "request_id": e.request_id,
                "kind": e.kind,
                "selected": e.selected,
                "confidence": e.confidence,
                "provider_name": e.provider_name,
                "timestamp": e.timestamp,
            }
            for e in self.decision_log.recent(limit)
        ]

    # --- Bounded test decision ---

    async def test_decision(self, context: str) -> Dict[str, Any]:
        """Run one bounded classification for the UI test button.

        Returns measured outcome only; mock results are explicitly labelled.
        """
        if not self.enabled:
            return {
                "executed": False,
                "reason": "JEV disabled — enable it first or keep using the standard pipeline.",
                "is_mock": True,
            }
        if not self.activation.should_activate(JevDecisionKind.TASK_CLASSIFICATION):
            return {
                "executed": False,
                "reason": "JEV activation policy disallows this decision kind.",
                "is_mock": bool(self.provider.is_mock),
            }
        if not self.budget.try_consume():
            self.tracker.record_fallback(self.provider.provider_name)
            return {
                "executed": False,
                "reason": "JEV request budget exhausted — using standard pipeline.",
                "is_mock": bool(self.provider.is_mock),
            }
        outcome = await self.router.classify_task(context, fallback="moderate")
        if outcome.source == "jev":
            self.tracker.record_success(
                self.provider.provider_name, outcome.latency_ms, len(context)
            )
        else:
            self.tracker.record_fallback(self.provider.provider_name)
        self.decision_log.record(
            request_id=f"ui-test-{self.tracker.summary(self.provider.provider_name)['requests']}",
            kind=JevDecisionKind.TASK_CLASSIFICATION.value,
            selected=outcome.selected,
            confidence=outcome.confidence,
            provider_name=self.provider.provider_name,
        )
        return {
            "executed": outcome.source == "jev",
            "selected": outcome.selected,
            "confidence": outcome.confidence,
            "reason": outcome.reason,
            "latency_ms": outcome.latency_ms,
            "is_mock": bool(self.provider.is_mock),
            "mock_warning": (
                "MOCK result — simulated, not a real JEV decision."
                if self.provider.is_mock
                else None
            ),
        }
