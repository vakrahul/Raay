"""Stage 4: agent advisor recommendations stay validated and read-only."""
import asyncio

import pytest

from src.orchestrator.jev.agent_advisor import JevAgentAdvisor
from src.orchestrator.jev.base import JevUnavailableError, JevValidationError
from src.orchestrator.jev.mock_adapter import MockJevAdapter
from src.orchestrator.jev.router import JevDecisionRouter, JevRouterConfig
from src.orchestrator.planner.agent_factory import DynamicAgentFactory
from src.orchestrator.planner.agent_registry import AgentRegistry


def _advisor(**cfg):
    router = JevDecisionRouter(MockJevAdapter(simulated_latency_ms=0), JevRouterConfig(**cfg))
    return JevAgentAdvisor(router, AgentRegistry())


def test_recommend_registered_agent_with_tools():
    async def _run():
        advisor = _advisor()
        before = len(advisor.registry.list_agents())
        rec = await advisor.recommend(
            "Fix the failing pytest suite.",
            ["agt_qa", "agt_researcher"],
            "agt_researcher",
        )
        assert rec.agent_id in ("agt_qa", "agt_researcher")
        agent = advisor.registry.get_agent(rec.agent_id)
        assert rec.authorized_tools == list(agent.authorized_tools)
        assert len(advisor.registry.list_agents()) == before  # read-only

    asyncio.run(_run())


def test_unknown_candidates_filtered_to_fallback():
    async def _run():
        advisor = _advisor()
        rec = await advisor.recommend("Do things.", ["ghost-1", "ghost-2"], "agt_researcher")
        assert rec.agent_id == "agt_researcher"
        assert advisor.registry.get_agent(rec.agent_id) is not None

    asyncio.run(_run())


def test_unregistered_fallback_rejected():
    async def _run():
        advisor = _advisor()
        with pytest.raises(JevValidationError):
            await advisor.recommend("Do things.", ["agt_qa"], "ghost")

    asyncio.run(_run())


def test_reuse_existing_agent_path():
    async def _run():
        advisor = _advisor()
        rec = await advisor.recommend(
            "Continue the analysis.",
            ["agt_qa", "agt_dataanalyst"],
            "agt_dataanalyst",
            existing_agent_id="agt_dataanalyst",
        )
        assert rec.agent_id in ("agt_dataanalyst", "agt_qa")
        assert isinstance(rec.reuse_existing, bool)

    asyncio.run(_run())


def test_split_recommendation_bool():
    async def _run():
        advisor = _advisor()
        assert isinstance(await advisor.recommend_split("Build a full-stack app."), bool)

    asyncio.run(_run())


def test_escalation_check_and_capacity_gate():
    async def _run():
        advisor = _advisor()
        rec = await advisor.recommend(
            "Delete the production database.",
            ["agt_database", "agt_qa"],
            "agt_qa",
            check_escalation=True,
        )
        assert isinstance(rec.escalate, bool)

        factory = DynamicAgentFactory(max_active_subagents=1)
        factory.spawn_subagent(
            parent_agent_id="coordinator",
            role_name="busy",
            purpose="fill pool",
            requested_tools=["fs_read_file"],
            custom_instructions="hold the slot",
        )
        gated = JevAgentAdvisor(advisor.router, advisor.registry, factory)
        rec2 = await gated.recommend("More work.", ["agt_qa"], "agt_qa")
        assert rec2.escalate is True
        assert len(factory.list_active_hierarchy()) == 1  # advisor spawned nothing

    asyncio.run(_run())


def test_timeout_falls_back_to_registered_default():
    async def _run():
        router = JevDecisionRouter(
            MockJevAdapter(simulated_latency_ms=2000), JevRouterConfig(timeout_ms=50)
        )
        advisor = JevAgentAdvisor(router, AgentRegistry())
        rec = await advisor.recommend("Hello.", ["agt_qa"], "agt_researcher")
        assert rec.agent_id == "agt_researcher"
        assert rec.source == "fallback"

    asyncio.run(_run())
