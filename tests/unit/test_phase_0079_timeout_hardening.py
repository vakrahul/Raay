"""PHASE 0079: executor timeouts fail the plan closed with checkpoints."""
import asyncio

from src.orchestrator.planner.coordinator import TaskCoordinator
from src.orchestrator.planner.task_models import TaskConstraints, TaskState


async def _never_returns(subtask, ctx):
    await asyncio.sleep(30)
    return "too late"


def test_executor_timeout_fails_plan():
    coord = TaskCoordinator()
    plan = coord.decompose_request("p79", "timeout demo")
    plan.get_subtask("task_1").constraints = TaskConstraints(
        allowed_tools=["fs_read_file"], max_duration_seconds=1
    )
    result = asyncio.run(coord.execute_plan(plan, _never_returns))
    assert result.state == TaskState.FAILED
    assert result.get_subtask("task_1").state == TaskState.FAILED
    assert result.get_subtask("task_1").error is not None
