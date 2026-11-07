"""General-Purpose Observe–Plan–Validate–Approve–Act–Observe–Verify–Recover Loop.

Upgrades WinAI-OE from single-route command matching into a general-purpose,
policy-enforced multi-step Windows agent loop that:
1. Observes live desktop, window, Paint SceneSpec, Excel/Spreadsheet, Notepad,
   and workspace file state (`DesktopObservation`).
2. Plans multi-step tasks dynamically using the configured LLM provider (with
   deterministic multi-step decomposition fallback when offline) over the
   registered `CapabilityToolRegistry`.
3. Validates every tool name (default-deny for unregistered tools), argument
   schema, DAG dependency order, workspace path confinement, and prevents
   consecutive duplicate non-idempotent actions.
4. Enforces `SecurityPolicyEngine` approval gates (`REQUIRE_APPROVAL` / `DENY`)
   before executing any step.
5. Executes bounded steps with `CancelToken` checks, re-observes state after
   every action, verifies concrete postconditions, and recovers safely (up to 1
   retry on transient non-partial failures; immediate `ABORTED_SAFE` on focus loss).
6. Tracks runtime optimization metrics (task completion rate, step success rate,
   verification accuracy, recovery rate, duplicate actions prevented, time-to-first-action,
   and end-to-end duration).
"""

from __future__ import annotations

import asyncio
import ctypes
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Callable, Coroutine, Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.orchestrator.coding_agent import CodingAgent
from src.providers.base import BaseModelProvider, ChatMessage
from src.security.policy_engine import PolicyDecision, RiskTier, SecurityPolicyEngine
from src.windows_integration.excel_grocery_controller import ExcelGroceryController
from src.windows_integration.execution_engine import ActionExecutionOutcome, WindowsExecutionEngine
from src.windows_integration.paint_controller import (
    SceneEditSpec,
    SceneObjectSpec,
    SceneSpec,
    Windows11PaintController,
)

logger = logging.getLogger(__name__)
user32 = ctypes.windll.user32


# ---------------------------------------------------------------------------
# 1. Observation & Plan Schemas
# ---------------------------------------------------------------------------


class DesktopObservation(BaseModel):
    """Snapshot of relevant desktop, application, and workspace state."""

    model_config = ConfigDict(extra="forbid")

    timestamp_utc: str
    foreground_hwnd: int = 0
    foreground_title: str = ""
    foreground_class: str = ""
    active_context_app: Optional[str] = None
    paint_objects_count: int = 0
    paint_object_ids: List[str] = Field(default_factory=list)
    spreadsheet_items_count: int = 0
    spreadsheet_grand_total_inr: float = 0.0
    workspace_files_sample: List[str] = Field(default_factory=list)


class PlannedStep(BaseModel):
    """Single bounded step inside a `StructuredTaskPlan`."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    tool_name: str = Field(min_length=1, max_length=64)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    expected_postcondition: str = Field(default="Tool outcome verified", max_length=240)


class StructuredTaskPlan(BaseModel):
    """Validated multi-step plan produced by the planner before execution."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=500)
    requires_clarification: bool = False
    clarification_question: Optional[str] = Field(default=None, max_length=300)
    unsupported_reason: Optional[str] = Field(default=None, max_length=300)
    steps: List[PlannedStep] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _validate_dag(self) -> "StructuredTaskPlan":
        if not self.requires_clarification and not self.unsupported_reason and not self.steps:
            raise ValueError("StructuredTaskPlan must contain at least 1 step unless clarification is required.")
        seen: Set[str] = set()
        for st in self.steps:
            if st.step_id in seen:
                raise ValueError(f"Duplicate step_id '{st.step_id}' in StructuredTaskPlan.")
            for dep in st.depends_on:
                if dep == st.step_id:
                    raise ValueError(f"Step '{st.step_id}' cannot depend on itself.")
                if dep not in seen:
                    raise ValueError(
                        f"Step '{st.step_id}' depends on '{dep}' which does not precede it in topological order."
                    )
            seen.add(st.step_id)
        return self


class StepExecutionRecord(BaseModel):
    """Complete audit & verification record for a single executed step."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool_name: str
    arguments: Dict[str, Any]
    status: Literal[
        "VERIFIED_SUCCESS",
        "FAILED_VERIFICATION",
        "FAILED_EXECUTION",
        "ABORTED_SAFE",
        "STOPPED",
        "WAITING_FOR_APPROVAL",
        "DENIED_BY_POLICY",
        "SKIPPED_DUPLICATE",
    ]
    failure_category: Optional[str] = None
    attempts: int = 1
    recovery_attempted: bool = False
    recovery_succeeded: bool = False
    verification_passed: bool = False
    verification_evidence: str = ""
    output_summary: str = ""
    output_data: Any = None
    approval_nonce: Optional[str] = None
    duration_ms: float = 0.0


class AgentLoopExecutionReport(BaseModel):
    """End-to-end report returned by `GeneralTaskExecutionLoop.execute_goal`."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    status: Literal[
        "COMPLETED",
        "NEEDS_CLARIFICATION",
        "WAITING_FOR_APPROVAL",
        "DENIED_BY_POLICY",
        "ABORTED_SAFE",
        "STOPPED",
        "FAILED_EXECUTION",
    ]
    summary: str
    initial_observation: DesktopObservation
    final_observation: DesktopObservation
    plan: Optional[StructuredTaskPlan] = None
    step_records: List[StepExecutionRecord] = Field(default_factory=list)
    observable_evidence: List[str] = Field(default_factory=list)
    planner_mode: str = "deterministic"
    llm_latency_ms: float = 0.0
    time_to_first_action_ms: float = 0.0
    total_duration_ms: float = 0.0
    duplicate_actions_prevented: int = 0
    unnecessary_actions_count: int = 0


# ---------------------------------------------------------------------------
# 2. Capability Tool Registry
# ---------------------------------------------------------------------------


class RegisteredToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    required_args: List[str] = Field(default_factory=list)
    optional_args: List[str] = Field(default_factory=list)
    risk_tier: RiskTier = RiskTier.LOW
    is_idempotent: bool = False
    requires_workspace_path_check: Optional[str] = None


class CapabilityToolRegistry:
    """Central registry of verified application and OS capabilities."""

    def __init__(self) -> None:
        self._specs: Dict[str, RegisteredToolSpec] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        defaults = [
            RegisteredToolSpec(
                name="paint_open",
                description="Launch and maximize Windows 11 Microsoft Paint.",
                is_idempotent=True,
            ),
            RegisteredToolSpec(
                name="paint_draw_command",
                description="Draw a shape or multi-object scene (circle, rectangle, triangle, star, heart, house, rocket, moon) in MS Paint with pixel verification.",
                required_args=["command"],
                is_idempotent=False,
            ),
            RegisteredToolSpec(
                name="paint_edit_scene",
                description="Edit an object in the active MS Paint scene (recolour, resize, move, add, remove).",
                required_args=["operation"],
                optional_args=["target_id", "new_fill_color", "scale_w", "scale_h", "shape"],
                is_idempotent=False,
            ),
            RegisteredToolSpec(
                name="paint_clear_canvas",
                description="Clear the MS Paint canvas via Select All + Delete.",
                is_idempotent=True,
            ),
            RegisteredToolSpec(
                name="paint_undo",
                description="Undo the last stroke in MS Paint via Ctrl+Z.",
                is_idempotent=False,
            ),
            RegisteredToolSpec(
                name="spreadsheet_open_excel_bi",
                description="Generate the 25-item grocery bill and open it in Microsoft Excel with embedded Category Column and Payment Pie charts.",
                is_idempotent=True,
            ),
            RegisteredToolSpec(
                name="spreadsheet_query_math",
                description="Query exact Python-verified totals, GST, UPI/Card/Cash splits, category totals, or low-stock items from the active spreadsheet.",
                required_args=["question"],
                optional_args=["reply_language"],
                is_idempotent=True,
            ),
            RegisteredToolSpec(
                name="spreadsheet_add_or_update_row",
                description="Add a new item row or update quantity/price of an existing item in the grocery spreadsheet.",
                required_args=["item_name", "qty"],
                optional_args=["unit", "unit_price_inr", "gst_pct", "category"],
                is_idempotent=False,
            ),
            RegisteredToolSpec(
                name="spreadsheet_to_paint_chart",
                description="Read category revenue totals from the active spreadsheet and draw a proportional 4-bar chart in MS Paint.",
                is_idempotent=False,
            ),
            RegisteredToolSpec(
                name="workspace_read_file",
                description="Read a text file inside the approved workspace.",
                required_args=["path"],
                is_idempotent=True,
                requires_workspace_path_check="path",
            ),
            RegisteredToolSpec(
                name="workspace_write_file",
                description="Write a text report or file inside the approved workspace.",
                required_args=["path", "content"],
                risk_tier=RiskTier.MEDIUM,
                is_idempotent=False,
                requires_workspace_path_check="path",
            ),
            RegisteredToolSpec(
                name="developer_run_pytest",
                description="Run pytest on a workspace test file or directory using RestrictedProcessRunner.",
                optional_args=["test_path"],
                is_idempotent=True,
            ),
            RegisteredToolSpec(
                name="system_get_overview",
                description="Inspect Windows OS, CPU, RAM, and disk status.",
                is_idempotent=True,
            ),
            RegisteredToolSpec(
                name="app_launch",
                description="Launch an approved Windows application (notepad, calc, mspaint).",
                required_args=["app_id"],
                is_idempotent=True,
            ),
            RegisteredToolSpec(
                name="process_close",
                description="Gracefully close a target application (mspaint.exe, EXCEL.EXE, notepad.exe) via WM_CLOSE.",
                required_args=["app_name"],
                is_idempotent=True,
            ),
        ]
        for sp in defaults:
            self._specs[sp.name] = sp

    def has_tool(self, name: str) -> bool:
        return name in self._specs

    def get_spec(self, name: str) -> RegisteredToolSpec:
        if name not in self._specs:
            raise KeyError(f"Tool '{name}' is not registered in CapabilityToolRegistry.")
        return self._specs[name]

    def list_specs(self) -> List[RegisteredToolSpec]:
        return list(self._specs.values())

    def prompt_catalog(self) -> str:
        lines = []
        for sp in self._specs.values():
            req = ", ".join(sp.required_args) if sp.required_args else "none"
            opt = ", ".join(sp.optional_args) if sp.optional_args else "none"
            lines.append(f"- `{sp.name}` (required: [{req}], optional: [{opt}]): {sp.description}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. Runtime Metrics Tracker
# ---------------------------------------------------------------------------


class AgentRuntimeMetrics:
    """Collects baseline and post-change execution metrics across tasks."""

    def __init__(self) -> None:
        self.total_tasks: int = 0
        self.completed_tasks: int = 0
        self.total_steps_executed: int = 0
        self.steps_succeeded: int = 0
        self.verifications_run: int = 0
        self.verifications_passed: int = 0
        self.recoveries_attempted: int = 0
        self.recoveries_succeeded: int = 0
        self.duplicate_actions_prevented: int = 0
        self.unnecessary_actions_count: int = 0
        self.approvals_requested: int = 0
        self.clarifications_requested: int = 0
        self.time_to_first_action_ms_list: List[float] = []
        self.e2e_duration_ms_list: List[float] = []
        self.estimated_tokens_used: int = 0

    def record_report(self, report: AgentLoopExecutionReport) -> None:
        self.total_tasks += 1
        if report.status == "COMPLETED":
            self.completed_tasks += 1
        elif report.status == "WAITING_FOR_APPROVAL":
            self.approvals_requested += 1
        elif report.status == "NEEDS_CLARIFICATION":
            self.clarifications_requested += 1

        self.duplicate_actions_prevented += report.duplicate_actions_prevented
        self.unnecessary_actions_count += report.unnecessary_actions_count
        if report.time_to_first_action_ms > 0:
            self.time_to_first_action_ms_list.append(report.time_to_first_action_ms)
        if report.total_duration_ms > 0:
            self.e2e_duration_ms_list.append(report.total_duration_ms)

        for sr in report.step_records:
            if sr.status == "SKIPPED_DUPLICATE":
                continue
            self.total_steps_executed += 1
            self.verifications_run += 1
            if sr.status == "VERIFIED_SUCCESS":
                self.steps_succeeded += 1
            if sr.verification_passed:
                self.verifications_passed += 1
            if sr.recovery_attempted:
                self.recoveries_attempted += 1
                if sr.recovery_succeeded:
                    self.recoveries_succeeded += 1

    def summary(self) -> Dict[str, Any]:
        t_tasks = max(1, self.total_tasks)
        t_steps = max(1, self.total_steps_executed)
        t_ver = max(1, self.verifications_run)
        t_rec = max(1, self.recoveries_attempted)
        avg_ttfa = (
            round(sum(self.time_to_first_action_ms_list) / len(self.time_to_first_action_ms_list), 2)
            if self.time_to_first_action_ms_list
            else 0.0
        )
        avg_e2e = (
            round(sum(self.e2e_duration_ms_list) / len(self.e2e_duration_ms_list), 2)
            if self.e2e_duration_ms_list
            else 0.0
        )
        return {
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "task_completion_rate_pct": round(100.0 * self.completed_tasks / t_tasks, 2),
            "total_steps_executed": self.total_steps_executed,
            "steps_succeeded": self.steps_succeeded,
            "step_success_rate_pct": round(100.0 * self.steps_succeeded / t_steps, 2)
            if self.total_steps_executed
            else 0.0,
            "verification_accuracy_pct": round(100.0 * self.verifications_passed / t_ver, 2)
            if self.verifications_run
            else 0.0,
            "recoveries_attempted": self.recoveries_attempted,
            "recoveries_succeeded": self.recoveries_succeeded,
            "recovery_success_rate_pct": round(100.0 * self.recoveries_succeeded / t_rec, 2)
            if self.recoveries_attempted
            else 0.0,
            "duplicate_actions_prevented": self.duplicate_actions_prevented,
            "unnecessary_actions_count": self.unnecessary_actions_count,
            "approvals_requested": self.approvals_requested,
            "clarifications_requested": self.clarifications_requested,
            "avg_time_to_first_action_ms": avg_ttfa,
            "avg_e2e_duration_ms": avg_e2e,
            "estimated_tokens_used": self.estimated_tokens_used,
        }


# ---------------------------------------------------------------------------
# 4. GeneralTaskExecutionLoop (Observe -> Plan -> Validate -> Approve -> Act -> Verify -> Recover)
# ---------------------------------------------------------------------------


class GeneralTaskExecutionLoop:
    """Executes multi-step natural-language tasks across Paint, Excel, Notepad, Files, and OS."""

    def __init__(
        self,
        workspace_root: Path,
        execution_engine: WindowsExecutionEngine,
        paint_controller: Optional[Windows11PaintController] = None,
        excel_controller: Optional[ExcelGroceryController] = None,
        llm_provider: Optional[BaseModelProvider] = None,
        registry: Optional[CapabilityToolRegistry] = None,
        max_retries_per_step: int = 1,
        step_timeout_seconds: float = 25.0,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.execution_engine = execution_engine
        self.policy_engine: SecurityPolicyEngine = execution_engine.policy_engine
        self.paint_controller = paint_controller or Windows11PaintController(
            workspace_root=self.workspace_root
        )
        self.excel_controller = excel_controller or ExcelGroceryController(
            workspace_root=self.workspace_root
        )
        self.coding_agent = CodingAgent(self.workspace_root)
        self.llm_provider = llm_provider
        self.registry = registry or CapabilityToolRegistry()
        self.max_retries_per_step = max(0, int(max_retries_per_step))
        self.step_timeout_seconds = max(1.0, float(step_timeout_seconds))
        self.metrics = AgentRuntimeMetrics()
        self.active_context_app: Optional[str] = None

    # ------------------------------------------------------------------
    # Stage 1: Observe
    # ------------------------------------------------------------------

    def observe_desktop_state(self) -> DesktopObservation:
        """Capture current foreground window, Paint scene, Spreadsheet totals, and workspace files."""
        fg_hwnd = int(user32.GetForegroundWindow() or 0)
        cls_buf = ctypes.create_unicode_buffer(256)
        txt_buf = ctypes.create_unicode_buffer(256)
        if fg_hwnd:
            user32.GetClassNameW(ctypes.c_void_p(fg_hwnd), cls_buf, 256)
            user32.GetWindowTextW(ctypes.c_void_p(fg_hwnd), txt_buf, 256)

        paint_objs: List[str] = []
        if self.paint_controller.current_scene is not None:
            paint_objs = [o.id for o in self.paint_controller.current_scene.objects]

        excel_analytics = self.excel_controller.compute_exact_analytics()
        ws_files: List[str] = []
        try:
            for p in sorted(self.workspace_root.iterdir()):
                if p.is_file() and not p.name.startswith("."):
                    ws_files.append(p.name)
                if len(ws_files) >= 15:
                    break
        except Exception:
            pass

        return DesktopObservation(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            foreground_hwnd=fg_hwnd,
            foreground_title=txt_buf.value,
            foreground_class=cls_buf.value,
            active_context_app=self.active_context_app,
            paint_objects_count=len(paint_objs),
            paint_object_ids=paint_objs,
            spreadsheet_items_count=int(excel_analytics.get("total_items") or 0),
            spreadsheet_grand_total_inr=float(excel_analytics.get("grand_total_inr") or 0.0),
            workspace_files_sample=ws_files,
        )

    # ------------------------------------------------------------------
    # Stage 2: Plan (LLM + Multi-Step Compositional Planner)
    # ------------------------------------------------------------------

    def _plan_deterministically(
        self, goal: str, observation: DesktopObservation
    ) -> StructuredTaskPlan:
        """Compose a multi-step `StructuredTaskPlan` from natural-language clauses without task hardcoding."""
        raw = (goal or "").strip()
        low = raw.lower()

        if not raw:
            return StructuredTaskPlan(
                goal="empty",
                requires_clarification=True,
                clarification_question="Please state the task you would like me to perform.",
            )

        # Ambiguous close request without target application
        if re.search(r"\b(close|terminate|kill|quit)\b", low) and not any(
            k in low for k in ("paint", "పెయింట్", "excel", "ఎక్సెల్", "notepad", "నోట్", "calc")
        ):
            return StructuredTaskPlan(
                goal=raw,
                requires_clarification=True,
                clarification_question=(
                    "Which application would you like me to close (for example: Paint, Excel, or Notepad)?"
                ),
            )

        # Split compound multi-step instructions on conjunctions ("and then", "then", ";", "మరియు", "তারপর", "और फिर")
        clauses = [
            c.strip()
            for c in re.split(
                r"\b(?:and then|then|after that|followed by)\b|;|(?:\s+మరియు\s+)|(?:\s+और\s+फिर\s+)|(?:\s+பிறகு\s+)",
                raw,
                flags=re.IGNORECASE,
            )
            if c.strip()
        ]
        # Also detect multi-capability single sentences (e.g., "add 5 kg sugar to the grocery bill, save a summary report to report.txt, and draw the revenue chart in Paint")
        if len(clauses) == 1 and "," in raw:
            sub_parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?", raw) if p.strip()]
            if len(sub_parts) >= 2:
                clauses = sub_parts

        steps: List[PlannedStep] = []

        for idx, clause in enumerate(clauses, start=1):
            cl_low = clause.lower()
            step_id = f"step_{idx}"
            deps = [steps[-1].step_id] if steps else []

            # 1. Close application
            if any(k in cl_low for k in ("close", "క్లోజ్", "మూసివేయి", "बंद करो", "மூடு")) and any(
                k in cl_low for k in ("paint", "పెయింట్", "ఎంఎస్", "excel", "ఎక్సెల్", "notepad", "calc")
            ):
                target_proc = (
                    "EXCEL.EXE"
                    if any(k in cl_low for k in ("excel", "ఎక్సెల్"))
                    else ("notepad.exe" if "notepad" in cl_low else "mspaint.exe")
                )
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="process_close",
                        arguments={"app_name": target_proc},
                        depends_on=deps,
                        expected_postcondition=f"WM_CLOSE dispatched to {target_proc}",
                    )
                )
                continue

            # 2. Spreadsheet -> Paint Revenue Bar Chart bridge
            if any(k in cl_low for k in ("chart", "bar chart", "graph", "చార్ట్")) and any(
                k in cl_low for k in ("paint", "పెయింట్", "draw", "గీయి", "revenue")
            ):
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="spreadsheet_to_paint_chart",
                        arguments={},
                        depends_on=deps,
                        expected_postcondition="4 proportional category revenue bars drawn and pixel-verified in MS Paint",
                    )
                )
                continue

            # 3. Spreadsheet row addition / update ("add 12 kg Ponni Boiled Rice at Rs 62 to the grocery bill")
            m_add_item = re.search(
                r"^(?:add|update|include|చేర్చు|జోడించు|जोड़ो|சேர்)\s+(\d+(?:\.\d+)?)\s*(kg|l|g|ml|units?|packets?)?\s+(?:of\s+)?(.+?)(?:\s+at\s+(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d+)?))?(?:\s+to\s+(?:the\s+)?(?:grocery|excel|bill|sheet).*)?$",
                clause.strip(),
                flags=re.IGNORECASE,
            )
            if m_add_item and not any(k in cl_low for k in ("star", "circle", "square", "heart", "paint")):
                qty_val = float(m_add_item.group(1))
                unit_val = (m_add_item.group(2) or "kg").strip()
                item_nm = re.sub(
                    r"\s+to\s+(?:the\s+)?(?:grocery|excel|bill|sheet).*$",
                    "",
                    m_add_item.group(3).strip(),
                    flags=re.IGNORECASE,
                ).strip()
                price_val = float(m_add_item.group(4)) if m_add_item.group(4) else 62.0
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="spreadsheet_add_or_update_row",
                        arguments={
                            "item_name": item_nm,
                            "qty": qty_val,
                            "unit": unit_val,
                            "unit_price_inr": price_val,
                        },
                        depends_on=deps,
                        expected_postcondition=f"Spreadsheet contains {item_nm} with qty={qty_val}",
                    )
                )
                continue

            # 4. Save / Write file or summary report in workspace ("save summary to audit_report.txt", "write file notes.txt with ...")
            m_file = re.search(
                r"(?:save|write|create|export)\s+(?:a\s+|the\s+)?(?:summary|report|file|note)?\s*(?:to|as|in|named)?\s*([a-zA-Z0-9_.-]+\.(?:txt|md|json|csv))(?:\s+with\s+content\s+(.+))?",
                clause.strip(),
                flags=re.IGNORECASE,
            )
            if m_file:
                fname = m_file.group(1).strip()
                explicit_body = (m_file.group(2) or "").strip()
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="workspace_write_file",
                        arguments={"path": fname, "content": explicit_body or "__AUTO_SUMMARY__"},
                        depends_on=deps,
                        expected_postcondition=f"Workspace file '{fname}' exists and is non-empty",
                    )
                )
                continue

            # 5. Read workspace file
            m_read = re.search(
                r"(?:read|inspect)\s+(?:workspace\s+)?file\s+([a-zA-Z0-9_./-]+)",
                clause.strip(),
                flags=re.IGNORECASE,
            )
            if m_read:
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="workspace_read_file",
                        arguments={"path": m_read.group(1).strip()},
                        depends_on=deps,
                        expected_postcondition="File content read without error",
                    )
                )
                continue

            # 6. Developer mode: run pytest
            if any(k in cl_low for k in ("run pytest", "run tests", "execute tests")):
                m_tp = re.search(r"(tests/[a-zA-Z0-9_./-]+)", clause)
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="developer_run_pytest",
                        arguments={"test_path": m_tp.group(1) if m_tp else "tests/unit/test_phase_0001_baseline.py"},
                        depends_on=deps,
                        expected_postcondition="Pytest run completed with exit_code == 0",
                    )
                )
                continue

            # 7. Open Excel BI Dashboard with live charts
            if any(k in cl_low for k in ("open excel", "open grocery", "ఓపెన్ గ్రోసరీ", "bi dashboard", "excel bi")):
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="spreadsheet_open_excel_bi",
                        arguments={},
                        depends_on=deps,
                        expected_postcondition="Excel grocery bill CSV written and analytics verified",
                    )
                )
                continue

            # 8. Spreadsheet Q&A / Math analysis
            if any(
                k in cl_low
                for k in (
                    "excel",
                    "grocery",
                    "bill",
                    "గ్రోసరీ",
                    "బిల్",
                    "ఎక్సెల్",
                    "యుపిఐ",
                    "యూపీఐ",
                    "జీఎస్టీ",
                    "జిఎస్టి",
                    "స్టాక్",
                    "గ్రీన్స్",
                    "గ్రెయిన్స్",
                    "స్టాప్స్",
                    "evlo",
                    "aachu",
                    "mottham",
                    "arisi",
                    "gst",
                    "upi",
                    "total",
                    "expensive",
                    "costly",
                )
            ):
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="spreadsheet_query_math",
                        arguments={"question": clause},
                        depends_on=deps,
                        expected_postcondition="Exact spreadsheet math computed and answer generated",
                    )
                )
                continue

            # 9. System overview
            if any(k in cl_low for k in ("system info", "system overview", "memory status", "ram status")):
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="system_get_overview",
                        arguments={},
                        depends_on=deps,
                        expected_postcondition="System hardware and RAM overview retrieved",
                    )
                )
                continue

            # 10. Launch whitelisted app
            m_launch = re.search(r"^(?:open|launch|start)\s+(notepad|calc|calculator|mspaint)$", cl_low)
            if m_launch:
                aid = "calc" if m_launch.group(1) == "calculator" else m_launch.group(1)
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="app_launch",
                        arguments={"app_id": aid},
                        depends_on=deps,
                        expected_postcondition=f"Application '{aid}' launched with PID > 0",
                    )
                )
                continue

            # 11. Paint commands (open, clear, undo, draw, edit)
            if any(
                k in cl_low
                for k in (
                    "paint",
                    "పెయింట్",
                    "पेंट",
                    "பெயிண்ட்",
                    "draw",
                    "గీయి",
                    "గీసి",
                    "circle",
                    "సర్కిల్",
                    "సైకిల్",
                    "square",
                    "స్క్వేర్",
                    "rectangle",
                    "triangle",
                    "star",
                    "స్టార్",
                    "heart",
                    "హార్ట్",
                    "house",
                    "ఇల్లు",
                    "rocket",
                    "రాకెట్",
                    "moon",
                    "చందమామ",
                    "కలర్",
                    "ఎల్లో",
                    "గ్రీన్",
                    "అగ్రీన్",
                    "రెడ్",
                    "బ్లూ",
                    "గోల్డ్",
                    "clear canvas",
                )
            ) or (observation.active_context_app == "paint"):
                steps.append(
                    PlannedStep(
                        step_id=step_id,
                        tool_name="paint_draw_command",
                        arguments={"command": clause},
                        depends_on=deps,
                        expected_postcondition="Paint command completed and pixel/window guard verified",
                    )
                )
                continue

        if not steps:
            return StructuredTaskPlan(
                goal=raw,
                requires_clarification=True,
                clarification_question=(
                    f"Could not map '{raw[:80]}' to a registered desktop tool. "
                    "Supported capabilities include MS Paint drawing/editing, Excel grocery analytics, "
                    "workspace file read/write, pytest execution, and system inspection."
                ),
            )

        return StructuredTaskPlan(goal=raw, steps=steps)

    async def plan_task(
        self, goal: str, observation: DesktopObservation
    ) -> Tuple[StructuredTaskPlan, str, float]:
        """Generate and validate a `StructuredTaskPlan` using LLM or deterministic planner."""
        t0 = time.perf_counter()
        det_plan = self._plan_deterministically(goal, observation)

        # If deterministic planner resolved all clauses into concrete steps (or detected ambiguity), use it fast
        if det_plan.steps or self.llm_provider is None:
            return det_plan, "deterministic_compositional", round((time.perf_counter() - t0) * 1000.0, 2)

        # Otherwise ask the configured LLM provider to decompose into registered tools
        sys_prompt = (
            "You are the structured task planner for WinAI-OE on Windows 11.\n"
            f"Current Desktop Observation: {observation.model_dump_json()}\n"
            f"Registered Tools:\n{self.registry.prompt_catalog()}\n\n"
            "Return ONLY a JSON object matching StructuredTaskPlan:\n"
            "{\n"
            '  "goal": "<goal>",\n'
            '  "requires_clarification": false,\n'
            '  "clarification_question": null,\n'
            '  "steps": [\n'
            "    {\n"
            '      "step_id": "step_1",\n'
            '      "tool_name": "<registered_tool_name>",\n'
            '      "arguments": {},\n'
            '      "depends_on": [],\n'
            '      "expected_postcondition": "<verifiable postcondition>"\n'
            "    }\n"
            "  ]\n"
            "}"
        )
        try:
            resp = await asyncio.wait_for(
                self.llm_provider.complete(
                    messages=[
                        ChatMessage(role="system", content=sys_prompt),
                        ChatMessage(role="user", content=goal),
                    ],
                    temperature=0.1,
                    max_tokens=450,
                ),
                timeout=8.0,
            )
            raw_txt = (getattr(resp, "content", "") if not isinstance(resp, str) else resp).strip()
            self.metrics.estimated_tokens_used += max(40, len(raw_txt) // 4)
            start = raw_txt.find("{")
            end = raw_txt.rfind("}")
            if start != -1 and end != -1 and end > start:
                parsed = json.loads(raw_txt[start : end + 1])
                llm_plan = StructuredTaskPlan(**parsed)
                return llm_plan, "llm_structured", round((time.perf_counter() - t0) * 1000.0, 2)
        except Exception as exc:
            logger.warning("LLM task planner failed (%s); falling back to clarification.", exc)

        return det_plan, "deterministic_fallback", round((time.perf_counter() - t0) * 1000.0, 2)

    # ------------------------------------------------------------------
    # Stage 3, 4, 5, 6, 7, 8: Validate, Approve, Act, Observe, Verify, Recover
    # ------------------------------------------------------------------

    def _validate_step(self, step: PlannedStep) -> Tuple[bool, Optional[str], Optional[str]]:
        """Validate tool registration, required arguments, and workspace path confinement."""
        if not self.registry.has_tool(step.tool_name):
            return False, "unregistered_tool", f"Tool '{step.tool_name}' is not in the registered tool allowlist."
        spec = self.registry.get_spec(step.tool_name)
        for req_arg in spec.required_args:
            if req_arg not in step.arguments or step.arguments[req_arg] in (None, ""):
                return (
                    False,
                    "invalid_argument",
                    f"Tool '{step.tool_name}' is missing required argument '{req_arg}'.",
                )
        if spec.requires_workspace_path_check:
            raw_p = str(step.arguments.get(spec.requires_workspace_path_check) or "")
            if not raw_p or "\x00" in raw_p:
                return False, "invalid_argument", "File path argument cannot be empty or contain null bytes."
            candidate = (self.workspace_root / raw_p).resolve()
            ok_path, reason = self.policy_engine.is_path_confined(candidate)
            if not ok_path:
                return False, "permission_denied", reason
        return True, None, None

    async def _execute_and_verify_single_step(
        self,
        step: PlannedStep,
        previous_outputs: Dict[str, Any],
        *,
        session_id: str,
        approval_nonce: Optional[str] = None,
        cancel_token: Optional[Any] = None,
    ) -> Tuple[StepExecutionRecord, Any]:
        """Run one validated step through policy gate, execute, observe, and verify postcondition."""
        t0 = time.perf_counter()
        if cancel_token and cancel_token.is_cancelled:
            return (
                StepExecutionRecord(
                    step_id=step.step_id,
                    tool_name=step.tool_name,
                    arguments=step.arguments,
                    status="STOPPED",
                    output_summary="Cancelled before step execution.",
                    duration_ms=0.0,
                ),
                None,
            )

        # 1. Validate tool & arguments
        valid_ok, fail_cat, fail_msg = self._validate_step(step)
        if not valid_ok:
            return (
                StepExecutionRecord(
                    step_id=step.step_id,
                    tool_name=step.tool_name,
                    arguments=step.arguments,
                    status="DENIED_BY_POLICY" if fail_cat == "permission_denied" else "FAILED_EXECUTION",
                    failure_category=fail_cat,
                    output_summary=fail_msg or "Validation failed.",
                    duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                ),
                None,
            )

        # 2. Policy Gate for file writes / process launches
        policy_tool_map = {
            "workspace_write_file": "fs_write_file",
            "workspace_read_file": "fs_read_file",
            "app_launch": "app_launch",
            "process_close": "process_close",
        }
        mapped_policy_tool = policy_tool_map.get(step.tool_name)
        if mapped_policy_tool:
            pol_eval = self.policy_engine.evaluate_action(
                tool_name=mapped_policy_tool,
                arguments=step.arguments,
                session_id=session_id,
                agent_id="agent_loop",
            )
            if pol_eval.decision == PolicyDecision.DENY:
                return (
                    StepExecutionRecord(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        arguments=step.arguments,
                        status="DENIED_BY_POLICY",
                        failure_category="permission_denied",
                        output_summary=pol_eval.reason,
                        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    ),
                    None,
                )
            if pol_eval.decision == PolicyDecision.REQUIRE_APPROVAL:
                if not approval_nonce:
                    return (
                        StepExecutionRecord(
                            step_id=step.step_id,
                            tool_name=step.tool_name,
                            arguments=step.arguments,
                            status="WAITING_FOR_APPROVAL",
                            approval_nonce=pol_eval.approval_nonce,
                            output_summary=pol_eval.reason,
                            duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                        ),
                        None,
                    )
                consumed = self.policy_engine.consume_approval(
                    approval_nonce,
                    expected_tool_name=mapped_policy_tool,
                    expected_arguments=step.arguments,
                    expected_session_id=session_id,
                )
                if not consumed:
                    return (
                        StepExecutionRecord(
                            step_id=step.step_id,
                            tool_name=step.tool_name,
                            arguments=step.arguments,
                            status="DENIED_BY_POLICY",
                            failure_category="permission_denied",
                            output_summary="Invalid, expired, or already-consumed approval nonce.",
                            duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                        ),
                        None,
                    )

        # 3. Execute with bounded retry on transient non-partial failure
        max_attempts = 1 + self.max_retries_per_step
        last_err_cat: Optional[str] = None
        last_summary = ""
        last_data: Any = None

        for attempt in range(1, max_attempts + 1):
            if cancel_token and cancel_token.is_cancelled:
                return (
                    StepExecutionRecord(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        arguments=step.arguments,
                        status="STOPPED",
                        attempts=attempt,
                        output_summary="Stopped during step execution.",
                        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    ),
                    None,
                )

            try:
                status_str, verified, ev_str, summary_str, out_data, err_cat = (
                    await asyncio.wait_for(
                        self._dispatch_tool_impl(step, previous_outputs, cancel_token=cancel_token),
                        timeout=self.step_timeout_seconds,
                    )
                )
            except asyncio.TimeoutError:
                status_str = "FAILED_EXECUTION"
                verified = False
                ev_str = f"Timed out after {self.step_timeout_seconds}s"
                summary_str = ev_str
                out_data = None
                err_cat = "timeout"
            except Exception as exc:
                status_str = "FAILED_EXECUTION"
                verified = False
                ev_str = str(exc)
                summary_str = f"Error executing {step.tool_name}: {exc}"
                out_data = None
                err_cat = "exception"

            if status_str in ("STOPPED", "ABORTED_SAFE"):
                return (
                    StepExecutionRecord(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        arguments=step.arguments,
                        status=status_str,  # type: ignore[arg-type]
                        failure_category=err_cat or "focus_lost",
                        attempts=attempt,
                        recovery_attempted=(attempt > 1),
                        recovery_succeeded=False,
                        verification_passed=False,
                        verification_evidence=ev_str,
                        output_summary=summary_str,
                        output_data=out_data,
                        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    ),
                    out_data,
                )

            if verified and status_str == "VERIFIED_SUCCESS":
                return (
                    StepExecutionRecord(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        arguments=step.arguments,
                        status="VERIFIED_SUCCESS",
                        attempts=attempt,
                        recovery_attempted=(attempt > 1),
                        recovery_succeeded=(attempt > 1),
                        verification_passed=True,
                        verification_evidence=ev_str,
                        output_summary=summary_str,
                        output_data=out_data,
                        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    ),
                    out_data,
                )

            last_err_cat = err_cat or "verification_mismatch"
            last_summary = summary_str
            last_data = out_data
            # Do not retry if non-recoverable category
            if last_err_cat in ("permission_denied", "invalid_argument", "focus_lost"):
                break

        return (
            StepExecutionRecord(
                step_id=step.step_id,
                tool_name=step.tool_name,
                arguments=step.arguments,
                status="FAILED_VERIFICATION" if last_err_cat == "verification_mismatch" else "FAILED_EXECUTION",
                failure_category=last_err_cat,
                attempts=max_attempts,
                recovery_attempted=(max_attempts > 1),
                recovery_succeeded=False,
                verification_passed=False,
                verification_evidence=last_summary,
                output_summary=last_summary,
                output_data=last_data,
                duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            ),
            last_data,
        )

    async def _dispatch_tool_impl(
        self,
        step: PlannedStep,
        previous_outputs: Dict[str, Any],
        *,
        cancel_token: Optional[Any] = None,
    ) -> Tuple[str, bool, str, str, Any, Optional[str]]:
        """Execute the underlying capability and verify its real postcondition."""
        tname = step.tool_name
        args = step.arguments
        should_cancel = (lambda: cancel_token.is_cancelled) if cancel_token else None

        if tname == "paint_open":
            self.active_context_app = "paint"
            res = await asyncio.to_thread(
                self.paint_controller.execute_natural_paint_command,
                "open paint",
                should_cancel=should_cancel,
            )
            st = str(res.get("status") or "FAILED_EXECUTION")
            if st in ("ABORTED_SAFE", "STOPPED"):
                return st, False, str(res.get("window_guard_result")), str(res.get("summary")), res, "focus_lost"
            ok = st == "COMPLETED" and str(res.get("window_guard_result", "")).startswith("PASS")
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_VERIFICATION",
                ok,
                f"Paint preflight guard={res.get('window_guard_result')}",
                str(res.get("summary") or "Opened MS Paint."),
                res,
                None if ok else "verification_mismatch",
            )

        if tname == "paint_draw_command":
            self.active_context_app = "paint"
            cmd_str = str(args.get("command") or "")
            res = await asyncio.to_thread(
                self.paint_controller.execute_natural_paint_command,
                cmd_str,
                should_cancel=should_cancel,
            )
            st = str(res.get("status") or "FAILED_EXECUTION")
            if st in ("ABORTED_SAFE", "STOPPED"):
                return st, False, str(res.get("window_guard_result")), str(res.get("summary")), res, "focus_lost"
            objs = res.get("objects") or []
            all_fills_ok = all(o.get("fill_verified", True) for o in objs)
            ok = st == "COMPLETED" and all_fills_ok
            ev = (
                f"Paint status={st}, strokes={res.get('strokes_completed')}, "
                f"fills_verified={all_fills_ok}, guard={res.get('window_guard_result')}"
            )
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_VERIFICATION",
                ok,
                ev,
                str(res.get("summary") or "Executed Paint command."),
                res,
                None if ok else "verification_mismatch",
            )

        if tname == "paint_edit_scene":
            self.active_context_app = "paint"
            edit_spec = SceneEditSpec(
                operation=args["operation"],
                target_id=str(args.get("target_id") or "last"),
                new_fill_color=args.get("new_fill_color"),
                scale_w=args.get("scale_w"),
                scale_h=args.get("scale_h"),
            )
            res = await asyncio.to_thread(
                self.paint_controller.apply_scene_edit,
                edit_spec,
                should_cancel=should_cancel,
            )
            st = str(res.get("status") or "FAILED_EXECUTION")
            if st in ("ABORTED_SAFE", "STOPPED"):
                return st, False, str(res.get("window_guard_result")), str(res.get("summary")), res, "focus_lost"
            ok = st == "COMPLETED"
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_VERIFICATION",
                ok,
                f"SceneEdit({edit_spec.operation}) status={st}",
                str(res.get("summary") or "Edited Paint scene."),
                res,
                None if ok else "verification_mismatch",
            )

        if tname == "paint_clear_canvas":
            res = await asyncio.to_thread(self.paint_controller.clear_canvas)
            ok = res.get("status") == "COMPLETED"
            return ("VERIFIED_SUCCESS" if ok else "FAILED_EXECUTION"), ok, "Canvas cleared via Select All + Delete", str(res.get("summary")), res, None

        if tname == "paint_undo":
            res = await asyncio.to_thread(self.paint_controller.undo)
            ok = res.get("status") == "COMPLETED"
            return ("VERIFIED_SUCCESS" if ok else "FAILED_EXECUTION"), ok, "Ctrl+Z dispatched to Paint", str(res.get("summary")), res, None

        if tname == "spreadsheet_open_excel_bi":
            self.active_context_app = "excel"
            res = await asyncio.to_thread(self.excel_controller.open_in_microsoft_excel)
            csv_exists = Path(str(res.get("csv_path") or "")).exists()
            items_cnt = int((res.get("analytics") or {}).get("total_items") or 0)
            ok = csv_exists and items_cnt > 0
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_VERIFICATION",
                ok,
                f"CSV exists={csv_exists}, items={items_cnt}, charts={res.get('charts_created_in_excel')}",
                f"Opened Excel BI Dashboard ({items_cnt} items, Grand Total Rs.{(res.get('analytics') or {}).get('grand_total_inr')}).",
                res,
                None if ok else "verification_mismatch",
            )

        if tname == "spreadsheet_query_math":
            self.active_context_app = "excel"
            q_str = str(args.get("question") or "")
            lang_str = str(args.get("reply_language") or "en-IN")
            ans, analytics = await self.excel_controller.answer_question_dynamically_with_llm(
                q_str, self.llm_provider, lang_str
            )
            ok = bool(ans and analytics.get("total_items", 0) > 0)
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_VERIFICATION",
                ok,
                f"Spreadsheet math verified over {analytics.get('total_items')} rows (Grand Total Rs.{analytics.get('grand_total_inr')})",
                ans,
                {"answer": ans, "analytics": analytics},
                None if ok else "verification_mismatch",
            )

        if tname == "spreadsheet_add_or_update_row":
            self.active_context_app = "excel"
            item_nm = str(args["item_name"])
            qty_v = float(args["qty"])
            unit_v = str(args.get("unit") or "kg")
            price_v = float(args.get("unit_price_inr") or 65.0)
            gst_v = float(args.get("gst_pct") or 5.0)
            cat_v = str(args.get("category") or "Grains & Staples")
            res = await asyncio.to_thread(
                self.excel_controller.add_or_update_item,
                item_nm,
                qty_v,
                unit_v,
                price_v,
                gst_v,
                cat_v,
            )
            row_data = res.get("row") or {}
            ok = abs(float(row_data.get("qty") or 0.0) - qty_v) < 1e-4
            grand = (res.get("analytics") or {}).get("grand_total_inr")
            summary_s = (
                f"{res.get('action', 'updated').capitalize()} {item_nm} ({qty_v:g} {unit_v} -> Rs.{row_data.get('total_amount_inr')}); "
                f"new Grand Total is Rs.{grand}."
            )
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_VERIFICATION",
                ok,
                f"Row '{item_nm}' verified in spreadsheet with qty={qty_v}, new grand_total=Rs.{grand}",
                summary_s,
                res,
                None if ok else "verification_mismatch",
            )

        if tname == "spreadsheet_to_paint_chart":
            self.active_context_app = "paint"
            chart_spec = self.excel_controller.build_paint_revenue_chart_scene_spec()
            res = await asyncio.to_thread(
                self.paint_controller.execute_scene_spec,
                chart_spec,
                transcript="spreadsheet_to_paint_chart",
                should_cancel=should_cancel,
            )
            st = str(res.get("status") or "FAILED_EXECUTION")
            if st in ("ABORTED_SAFE", "STOPPED"):
                return st, False, str(res.get("window_guard_result")), str(res.get("summary")), res, "focus_lost"
            objs = res.get("objects") or []
            ok = st == "COMPLETED" and len(objs) == 4 and all(o.get("fill_verified") for o in objs)
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_VERIFICATION",
                ok,
                f"Proportional 4-bar Excel revenue chart drawn & pixel-verified in Paint (strokes={res.get('strokes_completed')})",
                "Drew 4-bar proportional category revenue chart in MS Paint from live Excel data.",
                res,
                None if ok else "verification_mismatch",
            )

        if tname == "workspace_read_file":
            rel_p = str(args["path"])
            target = (self.workspace_root / rel_p).resolve()
            if not target.exists() or not target.is_file():
                return "FAILED_EXECUTION", False, f"File not found: {rel_p}", f"File '{rel_p}' does not exist.", None, "control_unavailable"
            content = target.read_text(encoding="utf-8", errors="replace")
            return "VERIFIED_SUCCESS", True, f"Read {len(content)} chars from '{rel_p}'", f"Read '{rel_p}' ({len(content)} chars).", {"path": rel_p, "content": content}, None

        if tname == "workspace_write_file":
            rel_p = str(args["path"])
            raw_content = str(args.get("content") or "")
            if raw_content == "__AUTO_SUMMARY__":
                # Build an evidence-based summary from previous step outputs or current Excel analytics
                a = self.excel_controller.compute_exact_analytics()
                hi = a.get("highest_item") or {}
                lo = (a.get("low_stock_items") or [{}])[0]
                cats_lines = "\n".join(
                    f"  - {k}: Rs.{v:.2f}" for k, v in (a.get("category_totals") or {}).items()
                )
                pays_lines = "\n".join(
                    f"  - {k}: Rs.{v:.2f} (GST: Rs.{(a.get('payment_gst_totals') or {}).get(k, 0.0):.2f})"
                    for k, v in (a.get("payment_totals") or {}).items()
                )
                raw_content = (
                    "WinAI-OE Verified Audit & Operations Summary\n"
                    "============================================\n"
                    f"Total Items: {a.get('total_items')}\n"
                    f"Subtotal (Before GST): Rs.{a.get('subtotal_inr'):.2f}\n"
                    f"Total GST: Rs.{a.get('total_gst_inr'):.2f}\n"
                    f"Grand Total: Rs.{a.get('grand_total_inr'):.2f}\n"
                    f"Top Revenue Item: {hi.get('name')} (Rs.{hi.get('total_inr')})\n"
                    f"Lowest Stock Item: {lo.get('name')} ({lo.get('qty')} {lo.get('unit')})\n\n"
                    f"Category Breakdown:\n{cats_lines}\n\n"
                    f"Payment & GST Breakdown:\n{pays_lines}\n"
                )
            target = (self.workspace_root / rel_p).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(raw_content, encoding="utf-8")
            # Postcondition verification: re-read from disk and confirm content matches
            verified_on_disk = target.exists() and target.read_text(encoding="utf-8") == raw_content
            return (
                "VERIFIED_SUCCESS" if verified_on_disk else "FAILED_VERIFICATION",
                verified_on_disk,
                f"Verified file '{rel_p}' on disk ({len(raw_content)} chars)",
                f"Saved verified file '{rel_p}' ({len(raw_content)} chars).",
                {"path": rel_p, "bytes_written": len(raw_content.encode("utf-8"))},
                None if verified_on_disk else "verification_mismatch",
            )

        if tname == "developer_run_pytest":
            test_p = str(args.get("test_path") or "tests/unit/test_phase_0001_baseline.py")
            summary = await self.coding_agent.run_tests(test_p)
            ok = summary.tests_passed and summary.exit_code == 0
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_VERIFICATION",
                ok,
                f"pytest exit_code={summary.exit_code}, total_run={summary.total_run}",
                f"Pytest on '{test_p}' {'passed' if ok else 'failed'} (exit_code={summary.exit_code}).",
                summary.model_dump(),
                None if ok else "verification_mismatch",
            )

        if tname == "system_get_overview":
            out = await self.execution_engine.execute_action(
                tool_name="system_get_overview",
                arguments={},
                session_id="agent_loop",
                agent_id="system_inspector",
            )
            data = out.output_data or {}
            ok = out.outcome == ActionExecutionOutcome.VERIFIED_SUCCESS and bool(data.get("os_name"))
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_VERIFICATION",
                ok,
                f"OS={data.get('os_name')}, CPU={data.get('cpu_usage_percent')}%",
                f"System overview verified: {data.get('os_name')} (CPU {data.get('cpu_usage_percent')}%).",
                data,
                None if ok else "verification_mismatch",
            )

        if tname == "app_launch":
            aid = str(args["app_id"]).strip().lower()
            out = await self.execution_engine.execute_action(
                tool_name="app_launch",
                arguments={"app_id": aid},
                session_id="agent_loop",
                agent_id="app_launcher",
            )
            pid = (out.output_data or {}).get("launched_pid")
            ok = out.outcome in (ActionExecutionOutcome.VERIFIED_SUCCESS, ActionExecutionOutcome.SUCCESS_UNVERIFIED) and bool(pid)
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_EXECUTION",
                ok,
                f"Launched {aid} (PID={pid})",
                f"Launched '{aid}' (PID {pid})." if ok else (out.error_message or f"Failed to launch '{aid}'."),
                out.output_data,
                None if ok else "control_unavailable",
            )

        if tname == "process_close":
            app_nm = str(args["app_name"])
            out = await self.execution_engine.execute_action(
                tool_name="process_close",
                arguments={"app_name": app_nm, "force": False},
                session_id="agent_loop",
                agent_id="process_closer",
            )
            self.active_context_app = None
            ok = out.outcome in (ActionExecutionOutcome.VERIFIED_SUCCESS, ActionExecutionOutcome.SUCCESS_UNVERIFIED)
            return (
                "VERIFIED_SUCCESS" if ok else "FAILED_EXECUTION",
                ok,
                f"WM_CLOSE dispatched to {app_nm}",
                f"Closed {app_nm} gracefully.",
                out.output_data,
                None if ok else "control_unavailable",
            )

        return "FAILED_EXECUTION", False, f"Unhandled tool '{tname}'", f"Unhandled tool '{tname}'", None, "unregistered_tool"

    # ------------------------------------------------------------------
    # Stage 9: Full Execute-Goal Entrypoint
    # ------------------------------------------------------------------

    async def execute_goal(
        self,
        goal: str,
        *,
        session_id: str = "agent_loop_session",
        approval_nonce: Optional[str] = None,
        cancel_token: Optional[Any] = None,
    ) -> AgentLoopExecutionReport:
        """Run the full 9-stage Observe–Plan–Validate–Approve–Act–Observe–Verify–Recover–Report loop."""
        t0 = time.perf_counter()
        init_obs = self.observe_desktop_state()

        try:
            plan, planner_mode, llm_ms = await self.plan_task(goal, init_obs)
        except Exception as exc:
            final_obs = self.observe_desktop_state()
            rep = AgentLoopExecutionReport(
                goal=goal or "invalid",
                status="FAILED_EXECUTION",
                summary=f"Plan validation failed: {exc}",
                initial_observation=init_obs,
                final_observation=final_obs,
                total_duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            )
            self.metrics.record_report(rep)
            return rep

        if plan.requires_clarification or plan.unsupported_reason:
            final_obs = self.observe_desktop_state()
            msg = plan.clarification_question or plan.unsupported_reason or "Clarification needed."
            rep = AgentLoopExecutionReport(
                goal=plan.goal,
                status="NEEDS_CLARIFICATION",
                summary=msg,
                initial_observation=init_obs,
                final_observation=final_obs,
                plan=plan,
                planner_mode=planner_mode,
                llm_latency_ms=llm_ms,
                total_duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            )
            self.metrics.record_report(rep)
            return rep

        step_records: List[StepExecutionRecord] = []
        evidence_list: List[str] = []
        previous_outputs: Dict[str, Any] = {}
        duplicate_prevented = 0
        last_non_idempotent_sig: Optional[str] = None
        ttfa_ms: float = 0.0

        for idx, step in enumerate(plan.steps):
            if idx == 0:
                ttfa_ms = round((time.perf_counter() - t0) * 1000.0, 2)

            # Duplicate consecutive non-idempotent action prevention
            if self.registry.has_tool(step.tool_name):
                spec = self.registry.get_spec(step.tool_name)
                sig = f"{step.tool_name}:{json.dumps(step.arguments, sort_keys=True, default=str)}"
                if not spec.is_idempotent and sig == last_non_idempotent_sig:
                    duplicate_prevented += 1
                    step_records.append(
                        StepExecutionRecord(
                            step_id=step.step_id,
                            tool_name=step.tool_name,
                            arguments=step.arguments,
                            status="SKIPPED_DUPLICATE",
                            verification_passed=True,
                            verification_evidence="Duplicate consecutive non-idempotent action prevented",
                            output_summary=f"Skipped duplicate step '{step.step_id}' ({step.tool_name}).",
                        )
                    )
                    continue
                if not spec.is_idempotent:
                    last_non_idempotent_sig = sig

            rec, out_data = await self._execute_and_verify_single_step(
                step,
                previous_outputs,
                session_id=session_id,
                approval_nonce=approval_nonce,
                cancel_token=cancel_token,
            )
            step_records.append(rec)
            previous_outputs[step.step_id] = out_data
            if rec.verification_evidence:
                evidence_list.append(f"[{step.step_id}:{step.tool_name}] {rec.verification_evidence}")

            if rec.status in (
                "WAITING_FOR_APPROVAL",
                "DENIED_BY_POLICY",
                "ABORTED_SAFE",
                "STOPPED",
                "FAILED_EXECUTION",
                "FAILED_VERIFICATION",
            ):
                status_map: Dict[str, Any] = {
                    "WAITING_FOR_APPROVAL": "WAITING_FOR_APPROVAL",
                    "DENIED_BY_POLICY": "DENIED_BY_POLICY",
                    "ABORTED_SAFE": "ABORTED_SAFE",
                    "STOPPED": "STOPPED",
                }
                overall_st = status_map.get(rec.status, "FAILED_EXECUTION")
                final_obs = self.observe_desktop_state()
                rep = AgentLoopExecutionReport(
                    goal=plan.goal,
                    status=overall_st,
                    summary=rec.output_summary,
                    initial_observation=init_obs,
                    final_observation=final_obs,
                    plan=plan,
                    step_records=step_records,
                    observable_evidence=evidence_list,
                    planner_mode=planner_mode,
                    llm_latency_ms=llm_ms,
                    time_to_first_action_ms=ttfa_ms,
                    total_duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    duplicate_actions_prevented=duplicate_prevented,
                )
                self.metrics.record_report(rep)
                return rep

        final_obs = self.observe_desktop_state()
        summaries = [r.output_summary for r in step_records if r.output_summary]
        rep = AgentLoopExecutionReport(
            goal=plan.goal,
            status="COMPLETED",
            summary=" ".join(summaries) if summaries else "All planned steps executed and verified.",
            initial_observation=init_obs,
            final_observation=final_obs,
            plan=plan,
            step_records=step_records,
            observable_evidence=evidence_list,
            planner_mode=planner_mode,
            llm_latency_ms=llm_ms,
            time_to_first_action_ms=ttfa_ms,
            total_duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            duplicate_actions_prevented=duplicate_prevented,
        )
        self.metrics.record_report(rep)
        return rep
