"""JEV agent orchestration advisor (Stage 4).

Read-only recommender over the existing agent registry and factory. It
never spawns agents, never mutates permissions, and never bypasses the
orchestrator: every recommendation names an already-registered agent,
carries that agent's tool allowlist for downstream enforcement, and
respects factory capacity limits. Spawning remains solely inside
DynamicAgentFactory with its own depth and pool guards.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from src.orchestrator.jev.base import JevUnavailableError, JevValidationError
from src.orchestrator.jev.router import JevDecisionRouter
from src.orchestrator.planner.agent_factory import DynamicAgentFactory
from src.orchestrator.planner.agent_registry import AgentRegistry


class AgentRecommendation(BaseModel):
    """Validated advisory output. Authorizes nothing by itself."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    source: str  # "jev" or "fallback"
    confidence: float
    rationale: str
    split_recommended: bool = False
    reuse_existing: bool = False
    escalate: bool = False
    authorized_tools: List[str] = []


class JevAgentAdvisor:
    """Recommends agents, splits, reuse, and escalation within hard limits."""

    def __init__(
        self,
        router: JevDecisionRouter,
        registry: Optional[AgentRegistry] = None,
        factory: Optional[DynamicAgentFactory] = None,
    ):
        self.router = router
        self.registry = registry or AgentRegistry()
        self.factory = factory

    def _registered(self, agent_id: str) -> bool:
        return self.registry.get_agent(agent_id) is not None

    def _pool_has_capacity(self) -> bool:
        if self.factory is None:
            return True
        return len(self.factory.list_active_hierarchy()) < self.factory.max_active_subagents

    async def recommend(
        self,
        task_description: str,
        candidate_agent_ids: List[str],
        fallback_agent_id: str,
        existing_agent_id: Optional[str] = None,
        check_escalation: bool = False,
    ) -> AgentRecommendation:
        """Recommend one registered agent for the task.

        Optionally considers reusing an existing agent and separately
        assesses escalation. Never creates agents or changes permissions.
        """
        if not self._registered(fallback_agent_id):
            raise JevValidationError(f"fallback agent '{fallback_agent_id}' is not registered")
        valid_candidates = [c for c in candidate_agent_ids if self._registered(c)]
        if not valid_candidates:
            valid_candidates = [fallback_agent_id]

        reuse_existing = False
        if existing_agent_id is not None and self._registered(existing_agent_id):
            reuse_outcome = await self.router.route_workflow(
                f"Reuse agent {existing_agent_id} for: {task_description}",
                ["reuse_existing", "spawn_new"],
                "reuse_existing",
            )
            if reuse_outcome.selected == "reuse_existing":
                reuse_existing = True
                chosen_id, source, confidence = existing_agent_id, reuse_outcome.source, reuse_outcome.confidence
            else:
                chosen_id, source, confidence = fallback_agent_id, "fallback", 0.0
        else:
            chosen_id, source, confidence = fallback_agent_id, "fallback", 0.0

        if not reuse_existing:
            selection = await self.router.select_agent(
                task_description, valid_candidates, fallback_agent_id
            )
            chosen_id = selection.selected if selection.selected in valid_candidates else fallback_agent_id
            source = selection.source if selection.selected in valid_candidates else "fallback"
            confidence = selection.confidence if selection.selected in valid_candidates else 0.0

        escalate = False
        if check_escalation:
            esc = await self.router.assess_escalation(task_description, fallback="escalate")
            escalate = esc.selected == "escalate"
            if esc.source == "fallback":
                escalate = True  # uncertain safety questions default to human review

        if not self._pool_has_capacity() and not reuse_existing:
            escalate = True

        agent = self.registry.get_agent(chosen_id)
        if agent is None:  # defensive: registry changed mid-flight
            raise JevUnavailableError(f"recommended agent '{chosen_id}' is no longer registered")
        return AgentRecommendation(
            agent_id=chosen_id,
            source=source,
            confidence=confidence,
            rationale=f"Validated against registry; pool capacity ok: {self._pool_has_capacity()}",
            split_recommended=False,
            reuse_existing=reuse_existing,
            escalate=escalate,
            authorized_tools=list(agent.authorized_tools),
        )

    async def recommend_split(self, task_description: str, fallback_split: bool = False) -> bool:
        """Recommend whether the task should be split into subtasks."""
        outcome = await self.router.route_workflow(
            f"Split decision for: {task_description}",
            ["execute_single", "split_subtasks"],
            "split_subtasks" if fallback_split else "execute_single",
        )
        return outcome.selected == "split_subtasks"
