"""Task representations, states, constraints, and DAG validation (Stage V)."""
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


class TaskState(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskConstraints(BaseModel):
    allowed_tools: List[str] = Field(default_factory=list)
    max_duration_seconds: int = 60
    requires_human_approval: bool = False


class SubTask(BaseModel):
    id: str
    title: str
    description: str
    assigned_role: str = "coder"
    dependencies: List[str] = Field(default_factory=list)
    state: TaskState = TaskState.PENDING
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    completion_criteria: str = "Result returned without error"
    result: Optional[str] = None
    error: Optional[str] = None


class TaskPlan(BaseModel):
    id: str
    goal: str
    subtasks: List[SubTask]
    state: TaskState = TaskState.PENDING
    active_subtask_index: int = 0

    def get_subtask(self, subtask_id: str) -> Optional[SubTask]:
        for st in self.subtasks:
            if st.id == subtask_id:
                return st
        return None

    def progress(self) -> Tuple[int, int, float]:
        """Return (completed, total, percent) without ever dividing by zero."""
        total = len(self.subtasks)
        if total == 0:
            return (0, 0, 0.0)
        done = sum(1 for st in self.subtasks if st.state == TaskState.COMPLETED)
        return (done, total, round(done / total * 100.0, 1))

    def ready_subtasks(self) -> List[SubTask]:
        """Return PENDING subtasks whose dependencies are all COMPLETED."""
        done = {st.id for st in self.subtasks if st.state == TaskState.COMPLETED}
        return [
            st
            for st in self.subtasks
            if st.state == TaskState.PENDING
            and all(dep in done for dep in st.dependencies)
        ]

    def validate_plan_dag(self) -> Tuple[bool, str]:
        """Verify plan has no cycles and dependencies exist."""
        subtask_ids = {st.id for st in self.subtasks}

        for st in self.subtasks:
            for dep in st.dependencies:
                if dep not in subtask_ids:
                    return False, f"Subtask '{st.id}' depends on missing task '{dep}'"
                if dep == st.id:
                    return False, f"Subtask '{st.id}' cannot depend on itself"

        # Check for circular dependencies using topological sort / DFS
        visited: Dict[str, int] = {} # 0: unvisited, 1: visiting, 2: visited

        def has_cycle(curr_id: str) -> bool:
            visited[curr_id] = 1
            curr_task = self.get_subtask(curr_id)
            if curr_task:
                for dep in curr_task.dependencies:
                    if visited.get(dep, 0) == 1:
                        return True
                    if visited.get(dep, 0) == 0:
                        if has_cycle(dep):
                            return True
            visited[curr_id] = 2
            return False

        for st in self.subtasks:
            if visited.get(st.id, 0) == 0:
                if has_cycle(st.id):
                    return False, f"Circular dependency detected involving task '{st.id}'"

        return True, "Plan DAG is valid"
