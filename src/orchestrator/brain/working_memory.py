"""Working Memory: In-session task state, active goals, and recent observations."""
from typing import Any, Dict, List, Optional
from src.orchestrator.brain.models import MemoryEntry, MemoryType, VerificationStatus


class WorkingMemory:
    """Manages ephemeral in-session task context and working scratchpad."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.current_goal: Optional[str] = None
        self.subtasks: List[str] = []
        self.variables: Dict[str, Any] = {}
        self.observations: List[MemoryEntry] = []

    def set_goal(self, goal: str, subtasks: Optional[List[str]] = None) -> None:
        self.current_goal = goal
        self.subtasks = subtasks or []

    def set_variable(self, key: str, value: Any) -> None:
        self.variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        return self.variables.get(key, default)

    def record_observation(
        self,
        observation: str,
        tool_name: Optional[str] = None,
        verification_status: VerificationStatus = VerificationStatus.VERIFIED_OBSERVATION,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            id=f"obs_{len(self.observations) + 1}",
            memory_type=MemoryType.WORKING,
            content=observation,
            verification_status=verification_status,
            metadata={"tool_name": tool_name} if tool_name else {},
        )
        self.observations.append(entry)
        return entry

    def recent_observations(self, limit: int = 5) -> List[MemoryEntry]:
        """Return the newest observations first, capped at limit."""
        if limit <= 0:
            return []
        return list(reversed(self.observations[-limit:]))

    def get_context_summary(self) -> str:
        """Produce condensed working memory prompt string."""
        lines = []
        if self.current_goal:
            lines.append(f"Goal: {self.current_goal}")
        if self.subtasks:
            lines.append(f"Subtasks: {', '.join(self.subtasks)}")
        if self.variables:
            lines.append(f"Variables: {self.variables}")
        if self.observations:
            lines.append("Recent Observations:")
            for obs in self.observations[-5:]:
                lines.append(f"- [{obs.verification_status.value}] {obs.content}")
        return "\n".join(lines)

    def clear(self) -> None:
        self.current_goal = None
        self.subtasks = []
        self.variables = {}
        self.observations = []
