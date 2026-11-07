"""PHASE 0076: supervision primitives exist in the coordinator."""
import inspect

from src.orchestrator.planner import coordinator as coord_mod
from src.orchestrator.planner.coordinator import TaskCoordinator


def test_supervision_primitives_present():
    src = inspect.getsource(TaskCoordinator.execute_plan)
    assert "asyncio.wait_for" in src
    assert "save_checkpoint" in src
    assert "max_duration_seconds" in src
    assert hasattr(coord_mod, "TaskCoordinator")
