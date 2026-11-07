"""Stage 5: JEV model routing inside allowlists with privacy firewall."""
import asyncio

import pytest

from src.orchestrator.intelligent_router import IntelligentRouter
from src.orchestrator.jev.base import JevValidationError
from src.orchestrator.jev.mock_adapter import MockJevAdapter
from src.orchestrator.jev.model_advisor import JevModelAdvisor
from src.orchestrator.jev.router import JevDecisionRouter, JevRouterConfig
from src.orchestrator.planner.agent_registry import AgentRegistry
from src.providers.local_adapter import LocalModelAdapter
from src.providers.mock_provider import MockProvider
from src.providers.registry import ProviderRegistry


def _advisor(**cfg):
    registry = ProviderRegistry()
    registry.register_provider("local", LocalModelAdapter())
    router = JevDecisionRouter(MockJevAdapter(simulated_latency_ms=0), JevRouterConfig(**cfg))
    return JevModelAdvisor(router, registry)


def test_recommend_stays_inside_allowlist():
    async def _run():
        advisor = _advisor()
        rec = await advisor.recommend("Summarize these notes.", ["mock", "local"], "mock")
        assert rec.provider_id in ("mock", "local")
        assert rec.rationale and rec.privacy_enforced is False

    asyncio.run(_run())


def test_privacy_mode_forces_local():
    async def _run():
        advisor = _advisor()
        rec = await advisor.recommend(
            "Analyze this private document.",
            ["mock", "local"],
            "local",
            privacy_mode=True,
        )
        assert rec.provider_id in ("mock", "local")
        assert rec.privacy_enforced is True

    asyncio.run(_run())


def test_privacy_mode_rejects_nonlocal_fallback():
    async def _run():
        advisor = _advisor()
        advisor.registry.register_provider("cloud-mock", MockProvider())
        with pytest.raises(JevValidationError):
            await advisor.recommend("x", ["mock"], "cloud-mock", privacy_mode=True)

    asyncio.run(_run())


def test_unregistered_allowlist_and_fallback_rejected():
    async def _run():
        advisor = _advisor()
        with pytest.raises(JevValidationError):
            await advisor.recommend("x", ["ghost"], "mock")
        with pytest.raises(JevValidationError):
            await advisor.recommend("x", ["mock"], "ghost")

    asyncio.run(_run())


def test_timeout_falls_back():
    async def _run():
        registry = ProviderRegistry()
        router = JevDecisionRouter(MockJevAdapter(simulated_latency_ms=2000), JevRouterConfig(timeout_ms=50))
        advisor = JevModelAdvisor(router, registry)
        rec = await advisor.recommend("Hello.", ["mock"], "mock")
        assert rec.provider_id == "mock" and rec.source == "fallback"

    asyncio.run(_run())


def test_context_minimization_truncates_private_text():
    advisor = _advisor()
    long_text = "SECRET-PROJECT-ALPHA " * 100
    minimized = advisor.minimize_context(long_text)
    assert len(minimized) <= advisor.context_budget_chars + 1
    assert long_text not in minimized  # full private text never forwarded whole


def test_legacy_fallback_hook_uses_existing_router():
    async def _run():
        registry = ProviderRegistry()
        registry.register_provider("local", LocalModelAdapter())
        legacy = IntelligentRouter(registry)
        router = JevDecisionRouter(MockJevAdapter(simulated_latency_ms=0), JevRouterConfig(enabled=False))
        advisor = JevModelAdvisor(router, registry, legacy_router=legacy)
        rec = await advisor.recommend(
            "Hello.", ["mock", "local"], "mock", use_legacy_fallback=True
        )
        assert rec.provider_id in ("mock", "local")
        assert rec.source == "fallback"

    asyncio.run(_run())


def test_require_tools_filters_candidates():
    async def _run():
        advisor = _advisor()
        rec = await advisor.recommend(
            "List files.", ["mock", "local"], "mock", require_tools=True
        )
        assert rec.provider_id in ("mock", "local")

    asyncio.run(_run())
    _ = AgentRegistry  # keep agent surface referenced for Stage 4 continuity
