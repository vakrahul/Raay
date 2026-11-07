"""Unit tests for Stage V: Task Planning and Multi-Agent Coordination (Phases 41-50)."""
import pytest
from src.orchestrator.planner.agent_registry import AgentRegistry, AgentRole
from src.orchestrator.planner.coordinator import TaskCoordinator
from src.orchestrator.planner.task_models import SubTask, TaskConstraints, TaskPlan, TaskState


@pytest.mark.unit
def test_plan_dag_validation_cycles():
    """Verify DAG validator accepts valid plans and rejects circular dependencies."""
    # 1. Valid Linear Plan
    valid_plan = TaskPlan(
        id="p1",
        goal="Test",
        subtasks=[
            SubTask(id="t1", title="Step 1", description="desc"),
            SubTask(id="t2", title="Step 2", description="desc", dependencies=["t1"]),
            SubTask(id="t3", title="Step 3", description="desc", dependencies=["t2"]),
        ],
    )
    is_valid, msg = valid_plan.validate_plan_dag()
    assert is_valid is True

    # 2. Circular Dependency (t1 -> t2 -> t1)
    cycle_plan = TaskPlan(
        id="p2",
        goal="Test",
        subtasks=[
            SubTask(id="t1", title="Step 1", description="desc", dependencies=["t2"]),
            SubTask(id="t2", title="Step 2", description="desc", dependencies=["t1"]),
        ],
    )
    is_valid, msg = cycle_plan.validate_plan_dag()
    assert is_valid is False
    assert "Circular dependency" in msg

    # 3. Missing dependency
    missing_plan = TaskPlan(
        id="p3",
        goal="Test",
        subtasks=[
            SubTask(id="t1", title="Step 1", description="desc", dependencies=["ghost_task"]),
        ],
    )
    is_valid, msg = missing_plan.validate_plan_dag()
    assert is_valid is False
    assert "missing task" in msg


@pytest.mark.unit
def test_agent_registry_least_privilege():
    """Verify specialized agent roles enforce least privilege on authorized tools."""
    registry = AgentRegistry()
    researcher = registry.get_by_role(AgentRole.RESEARCHER)
    assert researcher is not None
    assert "fs_write_file" not in researcher.authorized_tools
    assert "terminal_run" not in researcher.authorized_tools
    assert "fs_read_file" in researcher.authorized_tools

    coder = registry.get_by_role(AgentRole.CODER)
    assert coder is not None
    assert "fs_write_file" in coder.authorized_tools
    assert "terminal_run" not in coder.authorized_tools


@pytest.mark.asyncio
async def test_coordinator_execution_and_scoped_context():
    """Verify plan execution, message passing across dependencies, and result verification."""
    coordinator = TaskCoordinator()
    plan = coordinator.decompose_request("plan_001", "Implement security logging")

    executed_steps = []
    received_contexts = []

    async def mock_executor(subtask: SubTask, scoped_context: dict) -> str:
        executed_steps.append(subtask.id)
        received_contexts.append(scoped_context)
        return f"Completed output for {subtask.id}"

    completed_plan = await coordinator.execute_plan(plan, mock_executor)
    assert completed_plan.state == TaskState.COMPLETED
    assert executed_steps == ["task_1", "task_2", "task_3"]

    # task_2 should receive task_1 output
    assert "task_1" in received_contexts[1]
    assert received_contexts[1]["task_1"] == "Completed output for task_1"


@pytest.mark.asyncio
async def test_coordinator_checkpoint_and_failure_recovery():
    """Verify checkpoint saving and graceful failure handling."""
    coordinator = TaskCoordinator()
    plan = coordinator.decompose_request("plan_fail_test", "Broken goal")

    async def failing_executor(subtask: SubTask, scoped_context: dict) -> str:
        if subtask.id == "task_2":
            raise RuntimeError("Build failed on step 2")
        return "OK"

    failed_plan = await coordinator.execute_plan(plan, failing_executor)
    assert failed_plan.state == TaskState.FAILED
    assert failed_plan.get_subtask("task_1").state == TaskState.COMPLETED
    assert failed_plan.get_subtask("task_2").state == TaskState.FAILED
    assert failed_plan.get_subtask("task_3").state == TaskState.PENDING

    # Verify checkpoint can be retrieved
    checkpoint = coordinator.restore_checkpoint("plan_fail_test")
    assert checkpoint is not None
    assert checkpoint.get_subtask("task_1").state == TaskState.COMPLETED
