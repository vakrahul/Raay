"""Autonomous Task Planner and Goal Decomposition Engine (Phase 3).

Converts natural-language outcome requests into structured, verifiable execution plans
with explicit assumptions, dependency graphs, tool allowlists, and failure recovery policies.
"""

from enum import Enum
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


class PlanRiskLevel(str, Enum):
    LOW = "LOW"         # Read-only, data analysis, local inspection
    MEDIUM = "MEDIUM"   # Local file creation, running tests
    HIGH = "HIGH"       # Code modification, external requests, deployments
    CRITICAL = "CRITICAL"# Irreversible actions, database drops, permanent deletion


class AutonomousSubtask(BaseModel):
    subtask_id: str
    title: str
    description: str
    assigned_agent_role: str
    dependencies: List[str] = Field(default_factory=list)
    required_tools: List[str] = Field(default_factory=list)
    required_files: List[str] = Field(default_factory=list)
    expected_outputs: List[str] = Field(default_factory=list)
    permissions_required: List[str] = Field(default_factory=list)
    verification_criteria: str
    max_duration_seconds: int = 60
    failure_recovery_strategy: str = "Retry with corrected arguments, then fallback to human review"
    status: str = "PENDING"  # PENDING, IN_PROGRESS, COMPLETED, FAILED


class AutonomousExecutionPlan(BaseModel):
    plan_id: str
    original_prompt: str
    inferred_goal: str
    domain: str  # software_engineering, data_science, research, automation, security
    risk_level: PlanRiskLevel = PlanRiskLevel.MEDIUM
    subtasks: List[AutonomousSubtask] = Field(default_factory=list)
    recorded_assumptions: List[str] = Field(default_factory=list)
    missing_critical_decisions: List[str] = Field(default_factory=list)
    requires_user_clarification: bool = False
    max_token_budget: int = 15000
    created_at: float = Field(default_factory=time.time)

    def validate_plan_dag(self) -> Tuple[bool, str]:
        """Verify plan contains no cycles and all dependencies exist."""
        subtask_ids = {st.subtask_id for st in self.subtasks}
        for st in self.subtasks:
            for dep in st.dependencies:
                if dep not in subtask_ids:
                    return False, f"Subtask '{st.subtask_id}' depends on missing task '{dep}'"
                if dep == st.subtask_id:
                    return False, f"Subtask '{st.subtask_id}' cannot depend on itself"

        visited: Dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited

        def has_cycle(curr_id: str) -> bool:
            visited[curr_id] = 1
            curr_task = next((s for s in self.subtasks if s.subtask_id == curr_id), None)
            if curr_task:
                for dep in curr_task.dependencies:
                    if visited.get(dep, 0) == 1:
                        return True
                    if visited.get(dep, 0) == 0:
                        if has_cycle(dep):
                            return True
            visited[curr_id] = 2
            return False

        for st in self.subtasks:
            if visited.get(st.subtask_id, 0) == 0:
                if has_cycle(st.subtask_id):
                    return False, f"Cycle detected involving subtask '{st.subtask_id}'"

        return True, "Plan DAG is valid"


class AutonomousPlanner:
    """Transforms open-ended outcome goals into robust, verifiable multi-step execution plans."""

    def plan_outcome(self, user_prompt: str) -> AutonomousExecutionPlan:
        """Decompose natural language goal into domain-specialized execution plan."""
        plan_id = f"plan_{int(time.time() * 1000)}"
        prompt_lower = user_prompt.lower()

        # 1. Detect Domain & Risk
        domain = "general"
        risk = PlanRiskLevel.MEDIUM
        assumptions = []
        clarifications = []

        if any(k in prompt_lower for k in ["data science", "dataset", "csv", "eda", "exploratory data analysis", "clean the data", "train model"]):
            domain = "data_science"
            risk = PlanRiskLevel.MEDIUM
            assumptions.append("Safe operational assumption: Perform analysis inside local project workspace without external network egress.")
            subtasks = self._generate_data_science_plan(plan_id, user_prompt)

        elif any(k in prompt_lower for k in ["build a complete", "create a fastapi", "backend with", "implement the frontend", "new project"]):
            domain = "software_engineering"
            risk = PlanRiskLevel.HIGH
            assumptions.append("Safe operational assumption: Use Python 3.12, FastAPI, and Pytest as standard project stack.")
            subtasks = self._generate_software_engineering_plan(plan_id, user_prompt)

        elif any(k in prompt_lower for k in ["research", "report", "find opportunities", "investigate"]):
            domain = "research"
            risk = PlanRiskLevel.LOW
            assumptions.append("Safe operational assumption: Prioritize verified sources and distinct citation records.")
            subtasks = self._generate_research_plan(plan_id, user_prompt)

        elif any(k in prompt_lower for k in ["deploy", "publish", "delete", "destroy", "drop table"]):
            domain = "deployment_high_impact"
            risk = PlanRiskLevel.CRITICAL
            clarifications.append("Target production environment and credentials must be explicitly authorized by user.")
            subtasks = self._generate_deployment_plan(plan_id, user_prompt)

        else:
            domain = "general"
            subtasks = self._generate_general_plan(plan_id, user_prompt)

        return AutonomousExecutionPlan(
            plan_id=plan_id,
            original_prompt=user_prompt,
            inferred_goal=user_prompt,
            domain=domain,
            risk_level=risk,
            subtasks=subtasks,
            recorded_assumptions=assumptions,
            missing_critical_decisions=clarifications,
            requires_user_clarification=bool(clarifications),
            max_token_budget=15000,
        )

    def _generate_data_science_plan(self, plan_id: str, prompt: str) -> List[AutonomousSubtask]:
        return [
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st1",
                title="Inspect & Validate Dataset",
                description="Load dataset, check columns, shapes, null values, and data types",
                assigned_agent_role="data_analyst",
                dependencies=[],
                required_tools=["fs_read_file", "fs_list_files"],
                verification_criteria="Dataset shape, column summary, and missing values verified",
            ),
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st2",
                title="Data Cleaning & Feature Engineering",
                description="Handle nulls, encode categoricals, scale features, and save clean dataset",
                assigned_agent_role="data_scientist",
                dependencies=[f"{plan_id}_st1"],
                required_tools=["fs_write_file", "terminal_run"],
                verification_criteria="Cleaned dataset exported and verified without errors",
            ),
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st3",
                title="Model Training & Evaluation",
                description="Train candidate models, compute evaluation metrics (MAE, RMSE, F1), and produce evaluation report",
                assigned_agent_role="data_scientist",
                dependencies=[f"{plan_id}_st2"],
                required_tools=["terminal_run", "fs_read_file", "fs_write_file"],
                verification_criteria="Evaluation metrics computed on held-out test split",
            ),
        ]

    def _generate_software_engineering_plan(self, plan_id: str, prompt: str) -> List[AutonomousSubtask]:
        return [
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st1",
                title="Architecture Design & Plan Scaffolding",
                description="Define module structure, endpoints, schemas, and test plans",
                assigned_agent_role="software_engineer",
                dependencies=[],
                required_tools=["fs_write_file"],
                verification_criteria="Project directory structure and requirements defined",
            ),
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st2",
                title="Implementation & Unit Tests",
                description="Generate backend/frontend implementation files and comprehensive test suites",
                assigned_agent_role="backend_developer",
                dependencies=[f"{plan_id}_st1"],
                required_tools=["fs_write_file", "fs_read_file"],
                verification_criteria="Source code and test files written to workspace",
            ),
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st3",
                title="Automated Test Execution & Self-Healing",
                description="Execute test runner and fix any detected regressions",
                assigned_agent_role="qa_tester",
                dependencies=[f"{plan_id}_st2"],
                required_tools=["terminal_run", "fs_write_file"],
                verification_criteria="100% of unit and integration tests passing",
            ),
        ]

    def _generate_research_plan(self, plan_id: str, prompt: str) -> List[AutonomousSubtask]:
        return [
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st1",
                title="Gather Evidence & Sources",
                description="Search accessible web, papers, and local documents for topic",
                assigned_agent_role="researcher",
                dependencies=[],
                required_tools=["fs_read_file", "browser_navigate"],
                verification_criteria="At least 3 distinct verified sources collected",
            ),
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st2",
                title="Synthesize Comprehensive Report",
                description="Format findings into structured markdown report with citations and gaps",
                assigned_agent_role="documentation_specialist",
                dependencies=[f"{plan_id}_st1"],
                required_tools=["fs_write_file"],
                verification_criteria="Markdown report written and verified",
            ),
        ]

    def _generate_deployment_plan(self, plan_id: str, prompt: str) -> List[AutonomousSubtask]:
        return [
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st1",
                title="Pre-Flight Validation & Security Scan",
                description="Run all tests, static analysis, and verify 0 secret leaks",
                assigned_agent_role="security_reviewer",
                dependencies=[],
                required_tools=["terminal_run", "fs_read_file"],
                verification_criteria="All security and build checks passing",
            ),
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st2",
                title="Human Approval Gated Publication",
                description="Submit deployment plan to user for single-use approval nonce, then deploy",
                assigned_agent_role="deployment_preparer",
                dependencies=[f"{plan_id}_st1"],
                required_tools=["deploy_project"],
                permissions_required=["deploy:external"],
                verification_criteria="Human signature verified and deployment URL active",
            ),
        ]

    def _generate_general_plan(self, plan_id: str, prompt: str) -> List[AutonomousSubtask]:
        return [
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st1",
                title="Analyze Requirements",
                description=f"Inspect workspace and plan actions for: {prompt}",
                assigned_agent_role="researcher",
                dependencies=[],
                required_tools=["fs_read_file", "fs_list_files"],
                verification_criteria="Requirements established",
            ),
            AutonomousSubtask(
                subtask_id=f"{plan_id}_st2",
                title="Execute Actions & Verify",
                description="Perform permitted operations and confirm outcome",
                assigned_agent_role="software_engineer",
                dependencies=[f"{plan_id}_st1"],
                required_tools=["fs_write_file", "terminal_run"],
                verification_criteria="Outcome verified",
            ),
        ]
