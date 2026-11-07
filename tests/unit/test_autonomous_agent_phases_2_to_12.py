"""Comprehensive automated test suite for Autonomous Agent Phases 2 through 12."""
from pathlib import Path
import pytest

from src.orchestrator.brain.context_engine import (
    AdvancedContextEngine,
    MemoryQualityTier,
)
from src.orchestrator.planner.agent_registry import AgentRegistry, AgentRole
from src.orchestrator.planner.autonomous_planner import (
    AutonomousPlanner,
    PlanRiskLevel,
)
from src.orchestrator.project_builder import BuilderProjectType, ProjectBuilder
from src.orchestrator.verification_reporter import (
    EvidenceItem,
    ExecutionStage,
    HonestVerificationReporter,
    VerificationVerdict,
)
from src.security.approval_broker import ApprovalBroker
from src.security.permission_categories import (
    CategorizedPermissionManager,
    OperationCategory,
)
from src.security.policy_engine import PolicyDecision, SecurityPolicyEngine
from src.storage.task_state_engine import TaskStateEngine
from src.windows_integration.browser_session_manager import SharedBrowserSessionManager
from src.windows_integration.execution_engine import (
    ActionExecutionOutcome,
    WindowsExecutionEngine,
)


# --- PHASE 2: Advanced Context Layer Brain Tests ---
@pytest.mark.unit
def test_context_engine_lifecycle_and_provenance(temp_workspace: Path):
    """Verify task context, dynamic project file inspection, and provenance tagging."""
    # Write a dummy requirements.txt to test live inspection
    (temp_workspace / "requirements.txt").write_text("fastapi>=0.115.0\npytest>=8.0.0\n", encoding="utf-8")

    engine = AdvancedContextEngine(workspace_root=temp_workspace)

    # 1. Live project inspection
    proj = engine.inspect_project()
    assert "Python" in proj.languages
    assert "FastAPI" in proj.frameworks
    assert "python -m pytest tests" in proj.test_commands

    # 2. Task lifecycle
    task = engine.initialize_task("t_01", "Build URL Shortener", plan=["Design", "Code", "Test"])
    assert task.current_step == "Design"
    engine.advance_task_step("Design", "Schema established")
    assert task.current_step == "Code"
    assert len(task.completed_steps) == 1

    # 3. Provenance tiers
    engine.add_knowledge_item("Ground truth schema", MemoryQualityTier.VERIFIED_FACT, source="tests")
    engine.add_knowledge_item("Unverified guess", MemoryQualityTier.MODEL_HYPOTHESIS, source="llm")
    engine.add_knowledge_item("Failed attempt on SQLite memory", MemoryQualityTier.FAILED_APPROACH, source="test_fail")

    # Retrieval should filter out failed approaches for general queries
    results = engine.retrieve_relevant_context("schema", top_k=5)
    assert any(r.content == "Ground truth schema" for r in results)
    assert not any(r.quality_tier == MemoryQualityTier.FAILED_APPROACH for r in results)

    # 4. Compaction must preserve security directives and user goals
    compact = engine.build_compacted_context()
    assert "=== SECURITY & POLICY BOUNDARIES ===" in compact
    assert "Build URL Shortener" in compact


# --- PHASE 3: Autonomous Planner Tests ---
@pytest.mark.unit
def test_autonomous_planner_domains():
    """Verify natural language outcome planning across data science and software engineering."""
    planner = AutonomousPlanner()

    # 1. Data science plan
    ds_plan = planner.plan_outcome("Analyze this CSV dataset, perform exploratory data analysis, and train model")
    assert ds_plan.domain == "data_science"
    assert len(ds_plan.subtasks) == 3
    assert ds_plan.subtasks[0].assigned_agent_role == "data_analyst"
    is_valid, _ = ds_plan.validate_plan_dag()
    assert is_valid is True

    # 2. Software engineering plan
    se_plan = planner.plan_outcome("Build a complete FastAPI backend with PostgreSQL and React frontend")
    assert se_plan.domain == "software_engineering"
    assert se_plan.risk_level == PlanRiskLevel.HIGH
    assert any("FastAPI" in a for a in se_plan.recorded_assumptions)


# --- PHASE 4: 12 Specialized Agent Roles Test ---
@pytest.mark.unit
def test_agent_registry_12_roles():
    """Verify that all 12 specialized agent roles exist with explicit tool permissions."""
    registry = AgentRegistry()
    roles = [
        AgentRole.RESEARCHER,
        AgentRole.DATA_SCIENTIST,
        AgentRole.DATA_ANALYST,
        AgentRole.SOFTWARE_ENGINEER,
        AgentRole.FRONTEND_DEVELOPER,
        AgentRole.BACKEND_DEVELOPER,
        AgentRole.DATABASE_SPECIALIST,
        AgentRole.BROWSER_AUTOMATOR,
        AgentRole.QA_TESTER,
        AgentRole.SECURITY_REVIEWER,
        AgentRole.DOCUMENTATION_SPECIALIST,
        AgentRole.DEPLOYMENT_PREPARER,
    ]
    for r in roles:
        agent = registry.get_by_role(r)
        assert agent is not None, f"Agent role {r.value} is missing from registry"
        assert len(agent.authorized_tools) > 0
        assert agent.max_tokens >= 1024


# --- PHASE 5: Windows Autonomous Execution Engine Tests ---
@pytest.mark.asyncio
async def test_windows_execution_engine_observable_verification(temp_workspace: Path):
    """Verify 9-step execution cycle with pre/post state verification."""
    policy = SecurityPolicyEngine(workspace_root=temp_workspace, require_approvals=False)
    engine = WindowsExecutionEngine(workspace_root=temp_workspace, policy_engine=policy)

    # 1. Write file with verified state transition
    result = await engine.execute_action(
        tool_name="fs_write_file",
        arguments={"path": "observable_test.txt", "content": "hello world"},
        session_id="sess_1",
        agent_id="agt_tester",
    )
    assert result.outcome == ActionExecutionOutcome.VERIFIED_SUCCESS
    assert result.post_state["file_exists"] is True
    assert (temp_workspace / "observable_test.txt").exists()

    # 2. Blocked path escape
    deny_result = await engine.execute_action(
        tool_name="fs_read_file",
        arguments={"path": "../../outside_system.txt"},
        session_id="sess_1",
        agent_id="agt_tester",
    )
    assert deny_result.outcome == ActionExecutionOutcome.BLOCKED_POLICY


# --- PHASE 6: Shared Browser Session Manager Tests ---
@pytest.mark.asyncio
async def test_shared_browser_session_manager():
    """Verify exclusive lease acquisition, concurrency locking, and status."""
    manager = SharedBrowserSessionManager()

    # Agent 1 acquires session
    ok1 = await manager.acquire_session("agent_alpha", "task_1", timeout_seconds=1.0)
    assert ok1 is True
    status = manager.get_session_status()
    assert status["is_busy"] is True
    assert status["current_owner"] == "agent_alpha"

    # Agent 2 attempts concurrent acquisition -> must fail / timeout
    ok2 = await manager.acquire_session("agent_beta", "task_2", timeout_seconds=0.4)
    assert ok2 is False

    # Agent 1 releases
    released = await manager.release_session("agent_alpha")
    assert released is True
    assert manager.get_session_status()["is_busy"] is False


# --- PHASES 7 & 8: Project Builder Lifecycle Test ---
@pytest.mark.asyncio
async def test_project_builder_lifecycle(temp_workspace: Path):
    """Verify autonomous data science pipeline creation, tests, and recovery."""
    task_engine = TaskStateEngine(db_path=temp_workspace / "builder_tasks.db")
    builder = ProjectBuilder(base_workspace=temp_workspace, task_engine=task_engine)

    result = await builder.build_project(
        user_goal="Build data pipeline to clean and compute metrics",
        project_type=BuilderProjectType.DATA_SCIENCE,
        task_id="task_ds_01",
    )
    assert result.success is True
    assert result.tests_passed is True
    assert "src/data_pipeline.py" in result.files_created
    assert "tests/test_pipeline.py" in result.files_created

    # Verify task state persisted in SQLite
    saved = task_engine.load_task_state("task_ds_01")
    assert saved is not None
    assert saved.is_completed is True


# --- PHASES 9 & 10: Categorized Permissions Test ---
@pytest.mark.unit
def test_categorized_permission_manager(temp_workspace: Path):
    """Verify explicit operation categories (READ, WRITE, EXECUTE, EXTERNAL, HIGH_IMPACT) and taint escalation."""
    manager = CategorizedPermissionManager(workspace_root=temp_workspace)

    # 1. READ is allowed by default
    cat1, eval1 = manager.classify_and_evaluate("fs_read_file", "doc.txt")
    assert cat1 == OperationCategory.READ
    assert eval1.decision == PolicyDecision.ALLOW

    # 2. EXTERNAL always requires human approval
    cat2, eval2 = manager.classify_and_evaluate("send_email", "recruiter@company.com")
    assert cat2 == OperationCategory.EXTERNAL
    assert eval2.decision == PolicyDecision.REQUIRE_APPROVAL

    # 3. Session Taint Escalation: tainted session forces WRITE to require approval
    cat3, eval3 = manager.classify_and_evaluate("fs_write_file", "app.py", session_tainted=True)
    assert cat3 == OperationCategory.WRITE
    assert eval3.decision == PolicyDecision.REQUIRE_APPROVAL


# --- PHASES 11 & 12: Honest Verification Reporter Tests ---
@pytest.mark.unit
def test_honest_verification_reporter():
    """Verify ground-truth verdict calculation from observable evidence."""
    reporter = HonestVerificationReporter()

    # 1. Verified completed task
    evidence = [
        EvidenceItem(evidence_type="test_run", target="pytest tests", passed=True, details="5 passed in 0.4s"),
        EvidenceItem(evidence_type="file_exists", target="src/main.py", passed=True, details="Verified on disk"),
    ]
    report = reporter.generate_report(
        task_id="t_verify_01",
        goal="Implement Math Core",
        stage=ExecutionStage.COMPLETED,
        evidence=evidence,
        test_runs=5,
        test_fails=0,
    )
    assert report.verdict == VerificationVerdict.COMPLETED_AND_VERIFIED
    markdown = report.format_markdown()
    assert "COMPLETED_AND_VERIFIED" in markdown
    assert "✅" in markdown

    # 2. Failed task
    fail_report = reporter.generate_report(
        task_id="t_verify_02",
        goal="Broken Task",
        stage=ExecutionStage.FAILED,
        evidence=[],
        test_runs=2,
        test_fails=1,
    )
    assert fail_report.verdict == VerificationVerdict.FAILED
