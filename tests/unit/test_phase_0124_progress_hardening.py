"""PHASE 0124: progress never divides by zero nor exceeds bounds."""
from src.orchestrator.planner.task_models import TaskPlan, TaskState, SubTask


def test_empty_plan_progress_safe():
    plan = TaskPlan(id="empty", goal="g", subtasks=[])
    assert plan.progress() == (0, 0, 0.0)


def test_failed_states_do_not_count_as_done():
    plan = TaskPlan(id="p124", goal="g", subtasks=[
        SubTask(id="a", title="t", description="d"),
        SubTask(id="b", title="t", description="d"),
    ])
    plan.get_subtask("a").state = TaskState.FAILED
    done, total, pct = plan.progress()
    assert (done, total) == (0, 2) and pct == 0.0
