"""JEV decision routing layer (Stage 3).

Routes advisory decisions (classification, agent/model/tool selection,
workflow branching, retry, escalation, cost planning) through a JEV
provider with deterministic fallback. JEV is NEVER mandatory: when it is
disabled, unhealthy, slow, unconfident, or invalid, the caller-supplied
fallback is returned and the existing orchestration logic continues.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.orchestrator.jev.base import (
    BaseJevProvider,
    JevDecisionKind,
    JevDecisionRequest,
    JevTimeoutError,
    JevValidationError,
    decide_with_timeout,
)


class JevRouterConfig(BaseModel):
    """Operator configuration. JEV stays optional and bounded."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    confidence_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    timeout_ms: int = Field(default=500, ge=50, le=10000)
    check_health_first: bool = True


@dataclass
class DecisionOutcome:
    selected: str
    source: str  # "jev" or "fallback"
    confidence: float
    reason: str
    latency_ms: float = 0.0


@dataclass
class UseCaseMetrics:
    requests: int = 0
    jev_selected: int = 0
    fallbacks: int = 0
    timeouts: int = 0
    validation_failures: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.requests if self.requests else 0.0


class JevDecisionRouter:
    """Advisory router over a JEV provider with mandatory fallback paths."""

    def __init__(self, provider: BaseJevProvider, config: Optional[JevRouterConfig] = None):
        self.provider = provider
        self.config = config or JevRouterConfig()
        self._metrics: Dict[str, UseCaseMetrics] = {}

    def metrics(self) -> Dict[str, UseCaseMetrics]:
        return dict(self._metrics)

    def _record(self, use_case: str, outcome: DecisionOutcome, elapsed_ms: float) -> None:
        m = self._metrics.setdefault(use_case, UseCaseMetrics())
        m.requests += 1
        m.total_latency_ms += elapsed_ms
        if outcome.source == "jev":
            m.jev_selected += 1
        else:
            m.fallbacks += 1

    async def _decide(
        self,
        use_case: str,
        kind: JevDecisionKind,
        context: str,
        candidates: List[str],
        fallback: str,
        timeout_ms: Optional[int] = None,
    ) -> DecisionOutcome:
        started = time.perf_counter()
        metrics_key = use_case

        def _fallback(reason: str, timeout: bool = False, invalid: bool = False) -> DecisionOutcome:
            outcome = DecisionOutcome(
                selected=fallback,
                source="fallback",
                confidence=0.0,
                reason=reason,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
            m = self._metrics.setdefault(metrics_key, UseCaseMetrics())
            m.requests += 1
            m.total_latency_ms += outcome.latency_ms
            m.fallbacks += 1
            if timeout:
                m.timeouts += 1
            if invalid:
                m.validation_failures += 1
            return outcome

        if not self.config.enabled:
            return _fallback("JEV router disabled by configuration")
        if fallback not in candidates:
            return _fallback("fallback value not in candidate set", invalid=True)
        if self.config.check_health_first:
            try:
                healthy = await self.provider.health_check()
            except Exception:
                healthy = False
            if not healthy:
                return _fallback("JEV provider unhealthy")

        request = JevDecisionRequest(
            kind=kind,
            prompt_context=context,
            candidates=candidates,
            metadata={"use_case": use_case},
            timeout_ms=timeout_ms or self.config.timeout_ms,
        )
        try:
            response = await decide_with_timeout(self.provider, request)
        except JevTimeoutError as e:
            return _fallback(f"JEV timeout ({e})", timeout=True)
        except JevValidationError as e:
            return _fallback(f"JEV invalid response ({e})", invalid=True)
        except Exception as e:
            # A misbehaving provider must never crash the caller.
            return _fallback(f"JEV provider failure ({e.__class__.__name__})")

        if response.confidence < self.config.confidence_threshold:
            return _fallback(
                f"JEV confidence {response.confidence:.2f} below threshold {self.config.confidence_threshold:.2f}"
            )

        outcome = DecisionOutcome(
            selected=response.selected,
            source="jev",
            confidence=response.confidence,
            reason=f"JEV {self.provider.provider_name} decision",
            latency_ms=response.latency_ms,
        )
        self._record(metrics_key, outcome, outcome.latency_ms)
        return outcome

    # --- Supported use cases (each: input -> candidates -> fallback) ---

    async def classify_task(self, context: str, fallback: str = "moderate") -> DecisionOutcome:
        return await self._decide(
            "classify_task", JevDecisionKind.TASK_CLASSIFICATION, context,
            ["simple", "moderate", "complex"], fallback,
        )

    async def select_agent(self, context: str, candidates: List[str], fallback: str) -> DecisionOutcome:
        return await self._decide(
            "select_agent", JevDecisionKind.AGENT_SELECTION, context, candidates, fallback,
        )

    async def select_model(self, context: str, candidates: List[str], fallback: str) -> DecisionOutcome:
        return await self._decide(
            "select_model", JevDecisionKind.MODEL_SELECTION, context, candidates, fallback,
        )

    async def select_tool(self, context: str, candidates: List[str], fallback: str) -> DecisionOutcome:
        return await self._decide(
            "select_tool", JevDecisionKind.TOOL_SELECTION, context, candidates, fallback,
        )

    async def route_workflow(self, context: str, candidates: List[str], fallback: str) -> DecisionOutcome:
        return await self._decide(
            "route_workflow", JevDecisionKind.WORKFLOW_BRANCH, context, candidates, fallback,
        )

    async def decide_retry(self, context: str, fallback: str = "abort") -> DecisionOutcome:
        return await self._decide(
            "decide_retry", JevDecisionKind.WORKFLOW_BRANCH, context,
            ["retry", "fallback", "abort"], fallback,
        )

    async def assess_escalation(self, context: str, fallback: str = "escalate") -> DecisionOutcome:
        """Recommend proceed vs escalate. Escalation itself still requires
        the normal human-approval pipeline; this output authorizes nothing."""
        return await self._decide(
            "assess_escalation", JevDecisionKind.GUARDRAIL_ASSESSMENT, context,
            ["proceed", "escalate"], fallback,
        )

    async def plan_cost_aware(self, context: str, fallback: str = "balanced") -> DecisionOutcome:
        return await self._decide(
            "plan_cost_aware", JevDecisionKind.MODEL_SELECTION, context,
            ["cheap", "balanced", "frontier"], fallback,
        )
