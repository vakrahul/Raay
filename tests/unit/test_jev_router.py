"""Stage 3: decision routing with validated outcomes and fallbacks."""
import asyncio

import pytest

from src.orchestrator.jev.base import (
    BaseJevProvider,
    JevDecisionRequest,
    JevDecisionResponse,
)
from src.orchestrator.jev.mock_adapter import MockJevAdapter
from src.orchestrator.jev.router import DecisionOutcome, JevDecisionRouter, JevRouterConfig


def _router(**cfg):
    return JevDecisionRouter(MockJevAdapter(simulated_latency_ms=0), JevRouterConfig(**cfg))


def test_classify_task_validated():
    async def _run():
        out = await _router().classify_task("Refactor the authentication architecture.")
        assert isinstance(out, DecisionOutcome)
        assert out.selected in ("simple", "moderate", "complex")
        assert out.source in ("jev", "fallback")

    asyncio.run(_run())


def test_agent_model_tool_routing():
    async def _run():
        router = _router()
        agent = await router.select_agent("Fix failing pytest suite.", ["qa_tester", "researcher"], "qa_tester")
        assert agent.selected in ("qa_tester", "researcher")
        model = await router.select_model("Hi", ["cheap-local", "frontier"], "cheap-local")
        assert model.selected in ("cheap-local", "frontier")
        tool = await router.select_tool("Read the config file.", ["fs_read_file", "terminal_run"], "fs_read_file")
        assert tool.selected in ("fs_read_file", "terminal_run")

    asyncio.run(_run())


def test_workflow_retry_escalation_cost():
    async def _run():
        router = _router()
        assert (await router.route_workflow("Deploy preview?", ["deploy", "skip"], "skip")).selected in ("deploy", "skip")
        assert (await router.decide_retry("Timeout on first attempt.")).selected in ("retry", "fallback", "abort")
        assert (await router.assess_escalation("Delete production database.")).selected in ("proceed", "escalate")
        assert (await router.plan_cost_aware("Summarize notes.")).selected in ("cheap", "balanced", "frontier")

    asyncio.run(_run())


def test_disabled_router_always_falls_back():
    async def _run():
        router = _router(enabled=False)
        out = await router.classify_task("Anything at all.", fallback="moderate")
        assert out.source == "fallback" and out.selected == "moderate"

    asyncio.run(_run())


def test_timeout_falls_back_with_metric():
    async def _run():
        router = JevDecisionRouter(
            MockJevAdapter(simulated_latency_ms=2000),
            JevRouterConfig(timeout_ms=50),
        )
        out = await router.classify_task("Hello.", fallback="simple")
        assert out.source == "fallback" and out.selected == "simple"
        assert router.metrics()["classify_task"].timeouts == 1

    asyncio.run(_run())


def test_low_confidence_falls_back():
    async def _run():
        router = _router(confidence_threshold=0.99)
        out = await router.classify_task("Do something vague-ish.", fallback="moderate")
        assert out.source == "fallback" and out.selected == "moderate"

    asyncio.run(_run())


def test_invalid_fallback_value_falls_back_closed():
    async def _run():
        router = _router()
        out = await router.classify_task("Hello.", fallback="not-a-candidate")
        assert out.source == "fallback"
        assert router.metrics()["classify_task"].validation_failures == 1

    asyncio.run(_run())


class _UnhealthyProvider(MockJevAdapter):
    async def health_check(self):
        return False


class _LyingProvider(MockJevAdapter):
    async def decide(self, request):
        good = await super().decide(request)
        return JevDecisionResponse(
            request_id="wrong-id",
            kind=good.kind,
            selected=good.selected,
            probabilities=good.probabilities,
            confidence=good.confidence,
            latency_ms=1.0,
            provider_name="lying-jev",
            is_mock=True,
        )


def test_unhealthy_provider_falls_back():
    async def _run():
        router = JevDecisionRouter(_UnhealthyProvider())
        out = await router.classify_task("Hello.", fallback="simple")
        assert out.source == "fallback"

    asyncio.run(_run())


def test_malformed_provider_response_falls_back():
    async def _run():
        router = JevDecisionRouter(_LyingProvider())
        out = await router.classify_task("Hello.", fallback="simple")
        assert out.source == "fallback"
        assert router.metrics()["classify_task"].validation_failures == 1

    asyncio.run(_run())


def test_metrics_accumulate():
    async def _run():
        router = _router()
        await router.classify_task("Hello there friend.")
        await router.classify_task("Hello there friend.")
        m = router.metrics()["classify_task"]
        assert m.requests == 2
        assert m.jev_selected + m.fallbacks == 2
        assert m.avg_latency_ms >= 0.0

    asyncio.run(_run())


def test_ordinary_conversation_needs_no_jev():
    async def _run():
        router = _router(enabled=False)
        out = await router.classify_task("What time is it?", fallback="simple")
        assert out.selected == "simple"

    asyncio.run(_run())
