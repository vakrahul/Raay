"""Dynamic Sub-Agent Factory with Hierarchical Containment and Tool Scoping."""
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from src.orchestrator.planner.agent_registry import AgentRegistry, AgentRole, SpecializedAgent


class AgentHierarchySpec(BaseModel):
    agent_id: str
    parent_agent_id: Optional[str] = None
    role_name: str
    purpose: str
    depth: int = 1
    authorized_tools: List[str]
    system_prompt: str


class DynamicAgentFactory:
    """Safely spawns specialized sub-agents with strict recursive depth and tool limits."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        max_depth: int = 2,
        max_active_subagents: int = 5,
    ):
        self.registry = registry or AgentRegistry()
        self.max_depth = max_depth
        self.max_active_subagents = max_active_subagents
        # agent_id -> AgentHierarchySpec
        self._hierarchy: Dict[str, AgentHierarchySpec] = {}

    def spawn_subagent(
        self,
        parent_agent_id: str,
        role_name: str,
        purpose: str,
        requested_tools: List[str],
        custom_instructions: str,
    ) -> SpecializedAgent:
        """Spawn a specialized sub-agent strictly bounded by the parent's permissions."""
        # 1. Verify parent exists
        parent_spec = self._hierarchy.get(parent_agent_id)
        parent_depth = parent_spec.depth if parent_spec else 0
        parent_tools = set(parent_spec.authorized_tools if parent_spec else [
            "fs_read_file", "fs_write_file", "fs_list_files", "terminal_run", "browser_open_x"
        ])

        # 2. Check Depth Limit (Prevents infinite recursive agent spawning)
        child_depth = parent_depth + 1
        if child_depth > self.max_depth:
            raise PermissionError(
                f"Agent Spawning Blocked: Child depth {child_depth} exceeds maximum hierarchy limit ({self.max_depth})."
            )

        # 3. Check Sub-Agent Count Limit
        if len(self._hierarchy) >= self.max_active_subagents:
            raise ResourceWarning(
                f"Agent Pool Limit: Active sub-agents ({len(self._hierarchy)}) reached maximum threshold ({self.max_active_subagents})."
            )

        # 4. Enforce Strict Least-Privilege Tool Subsetting
        # A sub-agent cannot grant itself tools the parent does not possess
        disallowed_tools = set(requested_tools) - parent_tools
        if disallowed_tools:
            raise PermissionError(
                f"Privilege Escalation Blocked: Sub-agent requested unauthorized tools not held by parent: {disallowed_tools}"
            )

        # 5. Create specialized instance
        child_id = f"sub_{parent_agent_id}_{role_name}_{len(self._hierarchy) + 1}"
        system_prompt = (
            f"You are a specialized sub-agent ({role_name}) spawned by {parent_agent_id}.\n"
            f"Purpose: {purpose}\n"
            f"Instructions:\n{custom_instructions}\n"
            f"Security Boundary: You possess strictly scoped tools: {requested_tools}."
        )

        agent_instance = SpecializedAgent(
            role=AgentRole.CODER if "write" in str(requested_tools) else AgentRole.RESEARCHER,
            agent_id=child_id,
            display_name=f"SubAgent-{role_name.capitalize()}",
            system_prompt=system_prompt,
            authorized_tools=requested_tools,
        )

        # Register in hierarchy
        self._hierarchy[child_id] = AgentHierarchySpec(
            agent_id=child_id,
            parent_agent_id=parent_agent_id,
            role_name=role_name,
            purpose=purpose,
            depth=child_depth,
            authorized_tools=requested_tools,
            system_prompt=system_prompt,
        )

        self.registry.register_agent(agent_instance)
        return agent_instance

    def terminate_subagent(self, agent_id: str) -> bool:
        """Terminate a dynamic sub-agent and purge it from active hierarchy."""
        if agent_id in self._hierarchy:
            del self._hierarchy[agent_id]
            return True
        return False

    def list_active_hierarchy(self) -> List[AgentHierarchySpec]:
        return list(self._hierarchy.values())
