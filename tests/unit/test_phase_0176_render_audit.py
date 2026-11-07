"""PHASE 0176: dispatch result shape supports rendered confirmation."""
from src.orchestrator.task_dispatcher import AutonomousTaskDispatcher, TaskDispatchResult


def test_dispatch_result_shape():
    r = TaskDispatchResult(action_type="general", summary="ok", status="COMPLETED")
    assert r.observable_evidence == [] and r.details == {}
    assert hasattr(AutonomousTaskDispatcher, "execute_task")
