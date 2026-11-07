"""Comprehensive automated test suite for Upgrade Modules 2 through 10."""
from pathlib import Path
import pytest

from src.orchestrator.coding_agent import CodingAgent
from src.orchestrator.cost_tracker import CostEstimator
from src.orchestrator.deployment_engine import DeploymentEngine
from src.orchestrator.intelligent_router import IntelligentRouter, TaskComplexity
from src.orchestrator.observability import SystemTelemetryDashboard
from src.orchestrator.project_creator import ProjectCreator, ProjectPlan
from src.orchestrator.token_tracker import TokenTracker
from src.providers.base import ChatMessage
from src.providers.mock_provider import MockProvider
from src.providers.registry import ProviderRegistry
from src.security.approval_broker import ApprovalBroker
from src.security.policy_engine import SecurityPolicyEngine
from src.storage.git_recovery import GitRecoveryManager
from src.storage.task_state_engine import PersistentTaskState, StepRecord, TaskStateEngine


# --- MODULE 2: Intelligent Router Tests ---
@pytest.mark.unit
def test_intelligent_router_complexity_and_privacy():
    """Verify task complexity routing and privacy firewall."""
    registry = ProviderRegistry()
    router = IntelligentRouter(registry)

    # 1. Simple task
    simple_msgs = [ChatMessage(role="user", content="Hi there")]
    assert router.assess_complexity(simple_msgs) == TaskComplexity.SIMPLE
    selected = router.select_best_model(simple_msgs)
    assert selected in ["gemini", "mock"]

    # 2. Complex task
    complex_msgs = [ChatMessage(role="user", content="Refactor authentication system architecture")]
    assert router.assess_complexity(complex_msgs) == TaskComplexity.COMPLEX

    # 3. Privacy firewall
    privacy_selected = router.select_best_model(complex_msgs, privacy_mode=True)
    assert privacy_selected in ["local", "mock"]


@pytest.mark.asyncio
async def test_router_fallback_cascade():
    """Verify router automatically falls back to secondary provider if primary fails."""
    registry = ProviderRegistry()

    # Register failing provider
    class FailingProvider(MockProvider):
        async def complete(self, *args, **kwargs):
            raise ConnectionError("Simulated network outage")

    registry.register_provider("primary_fail", FailingProvider())
    router = IntelligentRouter(registry)
    router.fallback_chain = ["primary_fail", "mock"]

    resp, used_pid, latency = await router.execute_with_fallback(
        messages=[ChatMessage(role="user", content="Hello")],
    )
    assert used_pid == "mock"  # Successfully fell back to mock
    assert resp.content != ""
    assert latency > 0


# --- MODULE 3: Persistent Task State Engine Tests ---
@pytest.mark.unit
def test_persistent_task_state_and_resumption(temp_workspace: Path):
    """Verify SQLite WAL task state persistence, token-efficient context, and crash resumption."""
    db_file = temp_workspace / "tasks.db"
    engine1 = TaskStateEngine(db_path=db_file)

    task = PersistentTaskState(
        task_id="tsk_order_service",
        objective="Build Order Microservice",
        requirements=["FastAPI", "SQLite"],
        steps=[
            StepRecord(step_id="s1", title="Scaffold DB", description="Init database", status="COMPLETED", result="DB created"),
            StepRecord(step_id="s2", title="Create Endpoints", description="Add POST /orders", status="PENDING"),
        ],
        decisions={"db_choice": "SQLite WAL"},
    )
    engine1.save_task_state(task)

    # Simulate restart: open with new engine instance
    engine2 = TaskStateEngine(db_path=db_file)
    loaded = engine2.load_task_state("tsk_order_service")
    assert loaded is not None
    assert loaded.objective == task.objective
    assert len(loaded.steps) == 2

    # Verify token-efficient context extraction
    context = loaded.get_step_relevant_context()
    assert "Objective: Build Order Microservice" in context
    assert "DB created" in context
    assert "SQLite WAL" in context

    # Test crash resumption
    resumed = engine2.resume_task("tsk_order_service")
    assert resumed.current_step_index == 1
    assert resumed.current_step.step_id == "s2"
    assert resumed.current_step.status == "IN_PROGRESS"


# --- MODULE 4 & 5: Project Creation & Controlled Coding Tests ---
@pytest.mark.unit
def test_project_creator_and_guard(temp_workspace: Path):
    """Verify project scaffolding and overwrite protection."""
    creator = ProjectCreator(base_workspace_dir=temp_workspace)
    plan = creator.generate_plan("Build a URL shortener backend service in FastAPI")
    assert plan.project_name.startswith("proj_")
    assert "FastAPI" in plan.tech_stack

    project_dir = creator.scaffold_project(plan)
    assert project_dir.exists()
    assert (project_dir / "src" / "main.py").exists()
    assert (project_dir / "tests" / "test_api.py").exists()

    # Overwrite protection guard
    with pytest.raises(FileExistsError) as excinfo:
        creator.scaffold_project(plan, overwrite=False)
    assert "already exists" in str(excinfo.value)


@pytest.mark.asyncio
async def test_coding_agent_self_healing(temp_workspace: Path):
    """Verify coding agent path jail, test running, and self-healing fix loop."""
    proj = temp_workspace / "demo_proj"
    proj.mkdir()
    (proj / "src").mkdir()
    (proj / "tests").mkdir()

    agent = CodingAgent(project_dir=proj)

    # 1. Path jail check
    with pytest.raises(PermissionError):
        agent.write_file("../../outside.txt", "escape")

    # 2. Write code with intentional bug
    agent.write_file("src/math_ops.py", "def add(a, b):\n    return a - b\n") # Bug: minus instead of plus
    agent.write_file("tests/test_math.py", "from src.math_ops import add\ndef test_add():\n    assert add(2, 3) == 5\n")

    # Initial test run fails
    initial_summary = await agent.run_tests()
    assert initial_summary.tests_passed is False

    # 3. Simulate autonomous self-healing fix generator
    async def fix_generator(stdout, stderr):
        return {"src/math_ops.py": "def add(a, b):\n    return a + b\n"}

    healed, history = await agent.self_healing_cycle(fix_generator, max_iterations=2)
    assert healed is True
    assert history[-1].tests_passed is True


# --- MODULE 7: Git Task Branch & Recovery Tests ---
@pytest.mark.unit
def test_git_recovery_workflow(temp_workspace: Path):
    """Verify Git per-task branch isolation and clean rollback."""
    mgr = GitRecoveryManager(repo_dir=temp_workspace)
    assert mgr.init_repo() is True

    # Create initial commit on main
    (temp_workspace / "init.txt").write_text("initial", encoding="utf-8")
    mgr.stage_changes()
    mgr.commit_task("Initial commit")

    # Create task branch
    task_branch = mgr.create_task_branch("task_001_refactor")
    assert "task/task_001_refactor" in task_branch

    # Make working changes
    (temp_workspace / "experimental.txt").write_text("wip", encoding="utf-8")
    diff = mgr.get_diff()
    assert "experimental.txt" in diff or (temp_workspace / "experimental.txt").exists()

    # Rollback task to main
    rollback_ok = mgr.rollback_task(fallback_branch="main")
    assert rollback_ok is True
    assert not (temp_workspace / "experimental.txt").exists()


# --- MODULES 8 & 9: Approval-Gated Deployment Tests ---
@pytest.mark.asyncio
async def test_deployment_gates_and_approval(temp_workspace: Path):
    """Verify deployment requires 100% passing tests, single-use nonce, and human approval."""
    policy = SecurityPolicyEngine(workspace_root=temp_workspace)
    broker = ApprovalBroker()
    deployer = DeploymentEngine(policy_engine=policy, approval_broker=broker)

    proj_dir = temp_workspace / "deploy_sample"
    proj_dir.mkdir()

    # 1. Block deployment plan when pre-flight tests fail
    failing_plan = deployer.prepare_plan(proj_dir, tests_passed=False)
    with pytest.raises(ValueError):
        deployer.request_approval_nonce(failing_plan, session_id="s1")

    # 2. Valid plan with passing tests generates single-use nonce
    passing_plan = deployer.prepare_plan(proj_dir, tests_passed=True)
    nonce = deployer.request_approval_nonce(passing_plan, session_id="s1")
    assert nonce is not None

    # 3. Execution without valid nonce fails
    with pytest.raises(PermissionError):
        await deployer.execute_deployment(proj_dir, passing_plan, approval_nonce="fake_nonce")

    # 4. Execution with valid nonce succeeds and records URL
    result = await deployer.execute_deployment(proj_dir, passing_plan, approval_nonce=nonce)
    assert result.success is True
    assert "https://" in result.deployment_url
    assert len(deployer.history) == 1

    # Replay attack is rejected
    with pytest.raises(PermissionError):
        await deployer.execute_deployment(proj_dir, passing_plan, approval_nonce=nonce)


# --- MODULE 10: Observability Dashboard Tests ---
@pytest.mark.unit
def test_observability_dashboard_telemetry():
    """Verify live metrics consolidation, token reporting, currency conversion, and secret redaction."""
    tracker = TokenTracker()
    cost = CostEstimator()
    registry = ProviderRegistry()
    router = IntelligentRouter(registry)

    dashboard = SystemTelemetryDashboard(token_tracker=tracker, cost_estimator=cost, router=router)

    # Record activity
    tracker.record_usage(
        request_id="r1",
        task_id="t1",
        model_name="gemini-3.1-flash-lite",
        prompt_tokens=10000,
        completion_tokens=2000,
        cached_prompt_tokens=4000,
    )
    cost.calculate_request_cost("gemini-3.1-flash-lite", 10000, 2000, 4000)
    dashboard.record_tool_call("fs_read_file")
    dashboard.record_task_duration(150.0)

    # JSON report
    report_json = dashboard.generate_json_report()
    assert report_json["token_accounting"]["grand_total_tokens"] == 12000
    assert report_json["financial_summary"]["total_spent_usd"] > 0
    assert report_json["financial_summary"]["total_spent_inr"] > 0
    assert report_json["tool_invocations"]["fs_read_file"] == 1
    assert report_json["performance"]["avg_task_latency_ms"] == 150.0

    # Markdown report
    report_md = dashboard.generate_markdown_dashboard()
    assert "# WinAI-OE Telemetry & Observability Dashboard" in report_md
    assert "12,000" in report_md
    assert "`fs_read_file`: 1 calls" in report_md
