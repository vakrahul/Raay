"""PHASE 0080: supervision area sign-off via full mock execution."""


def test_supervision_area_signoff():
    import asyncio

    from src.orchestrator.planner.coordinator import TaskCoordinator
    from src.orchestrator.planner.task_models import TaskState

    async def _ok(subtask, ctx):
        return f"done:{subtask.id}"

    coord = TaskCoordinator()
    plan = coord.decompose_request("p80", "signoff")
    result = asyncio.run(coord.execute_plan(plan, _ok))
    assert result.state == TaskState.COMPLETED
    assert all(s.state == TaskState.COMPLETED for s in result.subtasks)
