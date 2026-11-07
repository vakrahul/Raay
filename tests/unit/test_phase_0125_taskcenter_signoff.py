"""PHASE 0125: task-center sign-off from 0% to 100% with checkpoint."""
from src.orchestrator.planner.coordinator import TaskCoordinator
from src.orchestrator.planner.task_models import TaskState


def test_taskcenter_area_signoff():
    coord = TaskCoordinator()
    plan = coord.decompose_request("p125", "goal")
    assert plan.progress() == (0, 3, 0.0)
    for st in plan.subtasks:
        st.state = TaskState.COMPLETED
    assert plan.progress() == (3, 3, 100.0)
    coord.save_checkpoint(plan)
    assert coord.restore_checkpoint("p125").progress() == (3, 3, 100.0)
