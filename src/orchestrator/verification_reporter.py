"""Honest Verification Reporter and Desktop Execution State Machine (Phases 11 & 12).

Distinguishes ground-truth verified outcomes from unverified inferences,
ensuring the AI environment never claims completion without observable evidence.
"""

from enum import Enum
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ExecutionStage(str, Enum):
    PLANNING = "PLANNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


AGENT_PANEL_STATUSES = (
    "Idle",
    "Planning",
    "ExecutingTool",
    "AwaitingApproval",
    "Completed",
    "Failed",
    "Terminated",
)

TERMINAL_PANEL_STATUSES = ("Completed", "Failed", "Terminated")


def is_known_panel_status(status: str) -> bool:
    """Return True for recognized agent activity panel states."""
    return status in AGENT_PANEL_STATUSES


class VerificationVerdict(str, Enum):
    COMPLETED_AND_VERIFIED = "COMPLETED_AND_VERIFIED"
    COMPLETED_UNVERIFIED = "COMPLETED_UNVERIFIED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    BLOCKED_BY_MISSING_INFO = "BLOCKED_BY_MISSING_INFO"


class EvidenceItem(BaseModel):
    evidence_type: str  # "test_run", "file_exists", "http_status", "observable_state", "user_confirmation"
    target: str
    passed: bool
    details: str
    timestamp: float = Field(default_factory=time.time)


class TaskVerificationReport(BaseModel):
    task_id: str
    goal: str
    verdict: VerificationVerdict
    execution_stage: ExecutionStage
    evidence_list: List[EvidenceItem] = Field(default_factory=list)
    tests_executed_count: int = 0
    tests_failed_count: int = 0
    files_modified: List[str] = Field(default_factory=list)
    external_actions_verified: List[str] = Field(default_factory=list)
    uncertainties_and_assumptions: List[str] = Field(default_factory=list)
    human_approvals_consumed: List[str] = Field(default_factory=list)
    completion_summary: str

    def format_markdown(self) -> str:
        lines = [
            f"# Verification Report: {self.goal}",
            f"**Verdict:** `{self.verdict.value}` | **Final Stage:** `{self.execution_stage.value}`\n",
            "## 1. Verified Evidence",
        ]
        if self.evidence_list:
            for ev in self.evidence_list:
                mark = "✅" if ev.passed else "❌"
                lines.append(f"* {mark} **[{ev.evidence_type.upper()}]** {ev.target}: {ev.details}")
        else:
            lines.append("*No automated evidence was captured.*")

        lines.extend([
            f"\n## 2. Test & Execution Metrics",
            f"* **Tests Executed:** {self.tests_executed_count}",
            f"* **Tests Failed:** {self.tests_failed_count}",
            f"* **Files Modified:** {len(self.files_modified)} files ({', '.join(self.files_modified[:5]) if self.files_modified else 'None'})",
        ])

        if self.external_actions_verified:
            lines.append("\n## 3. Verified External Actions")
            for act in self.external_actions_verified:
                lines.append(f"* 🌐 {act}")

        if self.uncertainties_and_assumptions:
            lines.append("\n## 4. Operational Assumptions & Uncertainties")
            for un in self.uncertainties_and_assumptions:
                lines.append(f"* ⚠️ {un}")

        lines.append(f"\n## 5. Final Assessment\n{self.completion_summary}")
        return "\n".join(lines)


class HonestVerificationReporter:
    """Evaluates task execution traces and produces factual, non-hallucinated verification reports."""

    def generate_report(
        self,
        task_id: str,
        goal: str,
        stage: ExecutionStage,
        evidence: List[EvidenceItem],
        files_changed: Optional[List[str]] = None,
        test_runs: int = 0,
        test_fails: int = 0,
        external_actions: Optional[List[str]] = None,
        assumptions: Optional[List[str]] = None,
        approvals: Optional[List[str]] = None,
    ) -> TaskVerificationReport:
        """Derive deterministic verdict strictly from observable evidence."""
        # Calculate verdict
        if stage == ExecutionStage.WAITING_FOR_APPROVAL:
            verdict = VerificationVerdict.WAITING_FOR_APPROVAL
            summary = "Task paused awaiting explicit user authorization nonce."
        elif stage == ExecutionStage.FAILED or test_fails > 0:
            verdict = VerificationVerdict.FAILED
            summary = f"Task execution failed or tests failed ({test_fails} errors)."
        elif stage == ExecutionStage.COMPLETED:
            if evidence and all(e.passed for e in evidence):
                verdict = VerificationVerdict.COMPLETED_AND_VERIFIED
                summary = "All steps executed and independently confirmed through observable evidence."
            else:
                verdict = VerificationVerdict.COMPLETED_UNVERIFIED
                summary = "Task completed without throwing errors, but lack independent test confirmation."
        else:
            verdict = VerificationVerdict.PARTIALLY_COMPLETED
            summary = f"Task currently in stage {stage.value}."

        return TaskVerificationReport(
            task_id=task_id,
            goal=goal,
            verdict=verdict,
            execution_stage=stage,
            evidence_list=evidence,
            tests_executed_count=test_runs,
            tests_failed_count=test_fails,
            files_modified=files_changed or [],
            external_actions_verified=external_actions or [],
            uncertainties_and_assumptions=assumptions or [],
            human_approvals_consumed=approvals or [],
            completion_summary=summary,
        )
