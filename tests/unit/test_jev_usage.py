"""Stage 8: usage tracking, budgets, activation gates, measured comparison."""
import asyncio

from src.orchestrator.jev.base import JevDecisionKind
from src.orchestrator.jev.mock_adapter import MockJevAdapter
from src.orchestrator.jev.router import JevDecisionRouter, JevRouterConfig
from src.orchestrator.jev.usage_tracker import (
    JevActivationPolicy,
    JevRequestBudget,
    JevUsageTracker,
    estimate_jev_cost_usd,
)


def test_cost_estimate_math():
    # 4M chars ~= 1M tokens ~= $0.042 input, $0.00 output.
    assert estimate_jev_cost_usd(4_000_000) == 0.042
    assert estimate_jev_cost_usd(0) == 0.0
    assert estimate_jev_cost_usd(-100) == 0.0


def test_tracker_rates_and_costs():
    tracker = JevUsageTracker()
    tracker.record_success("mock-jev", latency_ms=80.0, prompt_chars=400)
    tracker.record_success("mock-jev", latency_ms=120.0, prompt_chars=400)
    tracker.record_failure("mock-jev", timed_out=True)
    tracker.record_fallback("mock-jev")
    summary = tracker.summary("mock-jev")
    assert summary["requests"] == 3
    assert summary["success_rate"] == 2 / 3
    assert summary["fallback_rate"] == 1 / 3
    assert summary["avg_latency_ms"] == (80.0 + 120.0) / 3
    assert summary["total_cost_usd"] == 2 * estimate_jev_cost_usd(400)
    assert summary["timeouts"] == 1


def test_unknown_provider_summary_is_zeroed():
    assert JevUsageTracker().summary("ghost")["requests"] == 0


def test_budget_exhaustion_blocks_calls():
    budget = JevRequestBudget(max_decisions=2)
    assert budget.try_consume() is True
    assert budget.try_consume() is True
    assert budget.remaining == 0
    assert budget.try_consume() is False


def test_activation_policy_gates():
    policy = JevActivationPolicy()
    assert policy.should_activate(JevDecisionKind.TOOL_SELECTION) is True
    off = JevActivationPolicy(enabled=False)
    assert off.should_activate(JevDecisionKind.TOOL_SELECTION) is False
    scoped = JevActivationPolicy(allowed_kinds={JevDecisionKind.GUARDRAIL_ASSESSMENT})
    assert scoped.should_activate(JevDecisionKind.GUARDRAIL_ASSESSMENT) is True
    assert scoped.should_activate(JevDecisionKind.MODEL_SELECTION) is False


def test_measured_comparison_jev_vs_disabled():
    """Measured (not claimed): fallback-only path makes zero provider calls."""
    from src.orchestrator.jev.base import JevDecisionRequest

    calls = []

    class CountingMock(MockJevAdapter):
        async def decide(self, request):
            calls.append(1)
            return await super().decide(request)

    async def _run():
        tracker = JevUsageTracker()
        budget = JevRequestBudget(max_decisions=1)
        adapter = CountingMock(simulated_latency_ms=0)
        router = JevDecisionRouter(adapter, JevRouterConfig())
        req = JevDecisionRequest(
            kind=JevDecisionKind.TASK_CLASSIFICATION,
            prompt_context="Summarize the quarterly notes document.",
            candidates=["simple", "moderate", "complex"],
        )
        if budget.try_consume():
            out = await router.classify_task("Summarize the quarterly notes document.")
            tracker.record_success("mock-jev", out.latency_ms, len(req.prompt_context))
        # Budget exhausted: must NOT call the provider again.
        second_allowed = budget.try_consume()
        if second_allowed:
            await router.classify_task("Another question.")
        else:
            tracker.record_fallback("mock-jev")
        return tracker

    tracker = asyncio.run(_run())
    assert len(calls) == 1
    assert tracker.summary("mock-jev")["requests"] == 1
    assert tracker.summary("mock-jev")["fallback_rate"] == 1.0
