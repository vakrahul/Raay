"""PHASE 0078: plan cancellation transitions incomplete work cleanly."""
from src.orchestrator.planner.coordinator import TaskCoordinator
from src.orchestrator.planner.task_models import TaskState


def test_cancel_plan_marks_incomplete_cancelled():
    coord = TaskCoordinator()
    plan = coord.decompose_request("p78", "demo")
    plan.get_subtask("task_1").state = TaskState.COMPLETED
    cancelled = coord.cancel_plan(plan, reason="user stop")
    assert cancelled.state == TaskState.CANCELLED
    assert cancelled.get_subtask("task_1").state == TaskState.COMPLETED
    assert cancelled.get_subtask("task_2").state == TaskState.CANCELLED
    assert coord.restore_checkpoint("p78").state == TaskState.CANCELLED
