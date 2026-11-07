"""PHASE 0040: planner area sign-off across decompose, validate, checkpoint."""
from src.orchestrator.planner.coordinator import TaskCoordinator


def test_planner_area_signoff():
    coord = TaskCoordinator()
    plan = coord.decompose_request("p40", "signoff goal")
    ok, _ = plan.validate_plan_dag()
    assert ok is True
    coord.save_checkpoint(plan)
    restored = coord.restore_checkpoint("p40")
    assert restored is not None and restored.goal == "signoff goal"
    assert len(restored.subtasks) == 3
