"""Multi-Agent Task Coordinator, Execution Engine, and Recovery (Stage V)."""
import asyncio
from typing import Any, Callable, Coroutine, Dict, List, Optional
from src.orchestrator.planner.task_models import SubTask, TaskConstraints, TaskPlan, TaskState
from src.orchestrator.planner.agent_registry import AgentRegistry, AgentRole


class TaskCoordinator:
    """Executes multi-agent DAG task plans with checkpointing, verification, and recovery."""

    def __init__(self, agent_registry: Optional[AgentRegistry] = None):
        self.registry = agent_registry or AgentRegistry()
        self.checkpoints: Dict[str, TaskPlan] = {}

    def decompose_request(self, plan_id: str, goal: str) -> TaskPlan:
        """Decompose a high-level goal into structured subtasks with dependencies."""
        # Standard software engineering workflow decomposition
        subtasks = [
            SubTask(
                id="task_1",
                title="Inspect Project Context",
                description=f"Analyze relevant files for: {goal}",
                assigned_role="file_analyst",
                dependencies=[],
                constraints=TaskConstraints(allowed_tools=["fs_list_files", "fs_read_file"]),
                completion_criteria="Context gathered",
            ),
            SubTask(
                id="task_2",
                title="Implement Solution",
                description=f"Apply code modifications to achieve: {goal}",
                assigned_role="coder",
                dependencies=["task_1"],
                constraints=TaskConstraints(allowed_tools=["fs_write_file"], requires_human_approval=True),
                completion_criteria="Code written successfully",
            ),
            SubTask(
                id="task_3",
                title="Run Verification Tests",
                description="Execute test suite to confirm changes",
                assigned_role="test_runner",
                dependencies=["task_2"],
                constraints=TaskConstraints(allowed_tools=["terminal_run"]),
                completion_criteria="Tests passed with 0 errors",
            ),
        ]
        return TaskPlan(id=plan_id, goal=goal, subtasks=subtasks)

    def save_checkpoint(self, plan: TaskPlan) -> None:
        """Persist snapshot of plan state for crash recovery."""
        self.checkpoints[plan.id] = plan.model_copy(deep=True)

    def restore_checkpoint(self, plan_id: str) -> Optional[TaskPlan]:
        """Restore plan from last valid checkpoint."""
        cached = self.checkpoints.get(plan_id)
        return cached.model_copy(deep=True) if cached else None

    def cancel_plan(self, plan: TaskPlan, reason: str = "Cancelled by user") -> TaskPlan:
        """Cancel all incomplete subtasks and record a checkpoint."""
        for subtask in plan.subtasks:
            if subtask.state in (TaskState.PENDING, TaskState.IN_PROGRESS):
                subtask.state = TaskState.CANCELLED
                subtask.error = reason
        plan.state = TaskState.CANCELLED
        self.save_checkpoint(plan)
        return plan

    async def execute_plan(
        self,
        plan: TaskPlan,
        executor_func: Callable[[SubTask, Dict[str, str]], Coroutine[Any, Any, str]],
    ) -> TaskPlan:
        """Execute plan subtasks in dependency order, saving checkpoints and verifying results."""
        valid_dag, error_msg = plan.validate_plan_dag()
        if not valid_dag:
            plan.state = TaskState.FAILED
            raise ValueError(f"Invalid Plan DAG: {error_msg}")

        plan.state = TaskState.IN_PROGRESS
        self.save_checkpoint(plan)

        task_results: Dict[str, str] = {}

        for index, subtask in enumerate(plan.subtasks):
            plan.active_subtask_index = index

            # Check dependencies completed
            for dep in subtask.dependencies:
                dep_task = plan.get_subtask(dep)
                if not dep_task or dep_task.state != TaskState.COMPLETED:
                    subtask.state = TaskState.FAILED
                    subtask.error = f"Dependency '{dep}' was not completed successfully."
                    plan.state = TaskState.FAILED
                    self.save_checkpoint(plan)
                    return plan

            # Scoped shared context: provide only declared dependency results
            scoped_context = {dep: task_results[dep] for dep in subtask.dependencies if dep in task_results}

            subtask.state = TaskState.IN_PROGRESS
            try:
                # Execute subtask with timeout
                result = await asyncio.wait_for(
                    executor_func(subtask, scoped_context),
                    timeout=subtask.constraints.max_duration_seconds,
                )

                # Phase 49: Result Verification
                if not result or len(result.strip()) == 0:
                    raise ValueError(f"Task '{subtask.id}' produced empty result")

                subtask.result = result
                subtask.state = TaskState.COMPLETED
                task_results[subtask.id] = result
                self.save_checkpoint(plan)

            except Exception as e:
                subtask.state = TaskState.FAILED
                subtask.error = str(e)
                plan.state = TaskState.FAILED
                self.save_checkpoint(plan)
                return plan

        plan.state = TaskState.COMPLETED
        self.save_checkpoint(plan)
        return plan
