"""PHASE 0039: DAG validator rejects missing deps, self-deps, and cycles."""
from src.orchestrator.planner.task_models import SubTask, TaskPlan


def _plan(subtasks):
    return TaskPlan(id="p39", goal="g", subtasks=subtasks)


def test_missing_dependency_rejected():
    plan = _plan([SubTask(id="a", title="t", description="d", dependencies=["ghost"])])
    ok, msg = plan.validate_plan_dag()
    assert ok is False and "ghost" in msg


def test_self_dependency_rejected():
    plan = _plan([SubTask(id="a", title="t", description="d", dependencies=["a"])])
    ok, _ = plan.validate_plan_dag()
    assert ok is False


def test_cycle_rejected():
    plan = _plan([
        SubTask(id="a", title="t", description="d", dependencies=["b"]),
        SubTask(id="b", title="t", description="d", dependencies=["a"]),
    ])
    ok, msg = plan.validate_plan_dag()
    assert ok is False and "Circular" in msg
