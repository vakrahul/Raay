"""PHASE 0038: ready-subtask computation respects dependencies and states."""
from src.orchestrator.planner.coordinator import TaskCoordinator
from src.orchestrator.planner.task_models import TaskState


def test_ready_subtasks_follow_dag_order():
    coord = TaskCoordinator()
    plan = coord.decompose_request("p38", "demo goal")
    assert [s.id for s in plan.ready_subtasks()] == ["task_1"]
    plan.get_subtask("task_1").state = TaskState.COMPLETED
    assert [s.id for s in plan.ready_subtasks()] == ["task_2"]
    plan.get_subtask("task_2").state = TaskState.COMPLETED
    assert [s.id for s in plan.ready_subtasks()] == ["task_3"]
