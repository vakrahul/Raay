"""PHASE 0036: planner modules expose DAG validation and factory limits."""
import importlib


def test_planner_modules_importable():
    for name in (
        "src.orchestrator.planner.task_models",
        "src.orchestrator.planner.coordinator",
        "src.orchestrator.planner.agent_registry",
        "src.orchestrator.planner.agent_factory",
    ):
        assert importlib.import_module(name) is not None


def test_dag_validator_present():
    from src.orchestrator.planner.task_models import TaskPlan

    assert hasattr(TaskPlan, "validate_plan_dag")
