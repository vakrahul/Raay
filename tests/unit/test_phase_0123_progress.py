"""PHASE 0123: task progress fraction follows subtask states."""
from src.orchestrator.planner.coordinator import TaskCoordinator
from src.orchestrator.planner.task_models import TaskState


def test_progress_tracks_completion():
    coord = TaskCoordinator()
    plan = coord.decompose_request("p123", "goal")
    assert plan.progress() == (0, 3, 0.0)
    plan.get_subtask("task_1").state = TaskState.COMPLETED
    assert plan.progress() == (1, 3, 33.3)
