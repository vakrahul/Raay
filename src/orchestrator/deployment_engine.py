"""Approval-controlled deployment and publishing engine (Modules 8 & 9)."""
from abc import ABC, abstractmethod
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.security.approval_broker import ApprovalBroker
from src.security.policy_engine import SecurityPolicyEngine, PolicyDecision


class DeploymentPlan(BaseModel):
    project_name: str
    target_provider: str
    build_command: str
    output_directory: str
    pre_flight_tests_passed: bool
    requires_approval: bool = True


class DeploymentResult(BaseModel):
    success: bool
    deployment_url: Optional[str] = None
    deployment_id: str
    status: str
    logs: str
    deployed_at: float = Field(default_factory=time.time)


class BaseDeploymentAdapter(ABC):
    """Abstract deployment provider."""

    @abstractmethod
    async def deploy(self, project_dir: Path, plan: DeploymentPlan) -> DeploymentResult:
        pass


class MockDeploymentAdapter(BaseDeploymentAdapter):
    """Offline safe deployment adapter simulating static/cloud hosting."""

    async def deploy(self, project_dir: Path, plan: DeploymentPlan) -> DeploymentResult:
        deploy_id = f"dep_{int(time.time() * 1000)}"
        return DeploymentResult(
            success=True,
            deployment_url=f"https://{plan.project_name}.winai-preview.app",
            deployment_id=deploy_id,
            status="PUBLISHED",
            logs="Build succeeded. Static assets published to edge preview.",
        )


class DeploymentEngine:
    """Manages pre-flight verification, human approval gating, and publication."""

    def __init__(self, policy_engine: SecurityPolicyEngine, approval_broker: ApprovalBroker):
        self.policy_engine = policy_engine
        self.broker = approval_broker
        self.adapters: Dict[str, BaseDeploymentAdapter] = {
            "preview": MockDeploymentAdapter(),
            "mock": MockDeploymentAdapter(),
        }
        self.history: List[DeploymentResult] = []

    def prepare_plan(
        self,
        project_dir: Path,
        provider_name: str = "preview",
        tests_passed: bool = True,
    ) -> DeploymentPlan:
        """Analyze project and generate formal deployment plan."""
        return DeploymentPlan(
            project_name=project_dir.name,
            target_provider=provider_name,
            build_command="pytest tests -q",
            output_directory=str(project_dir),
            pre_flight_tests_passed=tests_passed,
            requires_approval=True,
        )

    def request_approval_nonce(self, plan: DeploymentPlan, session_id: str) -> str:
        """Generate a single-use CSPRNG approval nonce for deployment."""
        if not plan.pre_flight_tests_passed:
            raise ValueError("Pre-flight safety checks failed. Cannot request deployment approval.")

        req = self.broker.create_request(
            tool_name="deploy_project",
            target_resource=f"{plan.target_provider}://{plan.project_name}",
            parameters=plan.model_dump(),
            risk_tier="CRITICAL",
            reason=f"Publishing project '{plan.project_name}' to {plan.target_provider} requires explicit authorization.",
            session_id=session_id,
            agent_id="agt_deployer",
        )
        return req.nonce

    async def execute_deployment(
        self,
        project_dir: Path,
        plan: DeploymentPlan,
        approval_nonce: str,
    ) -> DeploymentResult:
        """Execute deployment strictly after verifying and consuming the approval nonce."""
        verified = self.broker.verify_and_consume(approval_nonce)
        if not verified:
            raise PermissionError("Deployment blocked: Invalid, expired, or replayed approval nonce.")

        adapter = self.adapters.get(plan.target_provider, self.adapters["preview"])
        result = await adapter.deploy(project_dir, plan)
        self.history.append(result)
        return result
