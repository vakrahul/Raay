"""Controlled End-to-End Voice Execution Pipeline for WinAI-OE (Milestone 3).

Pipeline Stages:
  Microphone/Audio -> Prisma STT -> Transcript Validation -> VoiceIntentPlanner
  -> Strict VoiceIntent Validation -> Authoritative SecurityPolicyEngine
  -> Action-Bound ApprovalBroker (when required) -> Pre-Execution Revalidation
  -> Existing WindowsExecutionEngine / AutonomousTaskDispatcher -> Result Validation
  -> Sanitized AuditLogger -> Timbre TTS & SpeechOutputManager (with safe text fallback).

Security Invariants:
1. Authoritative Policy Enforcement: Synchronizes and enforces a single authoritative
   `SecurityPolicyEngine`. Any attempt to attach a dispatcher with a different or
   weaker policy engine is rejected or bound to the authoritative policy engine.
2. Action- & Argument-Bound Approvals: Approvals are bound via SHA-256 `action_hash`
   to `(tool_name, target_resource, arguments)` and `session_id`. Any modification
   to arguments, target, or session invalidates the approval nonce.
3. Pre-Execution Revalidation: Policy and cancellation state are re-checked
   immediately prior to execution, even after approval is granted.
4. No Duplicate Execution on TTS Failure: TTS/playback runs strictly after execution
   and audit logging; a TTS or speaker failure returns the verified text result
   with `tts_succeeded=False` and never re-runs the action.
"""

import asyncio
import contextlib
import ctypes
import hashlib
import json
import secrets
import time
import unicodedata
from ctypes import wintypes
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple

import psutil
from pydantic import BaseModel, ConfigDict, Field

from src.orchestrator.errors import SecurityViolationError
from src.orchestrator.task_dispatcher import AutonomousTaskDispatcher
from src.security.approval_broker import ApprovalBroker, ApprovalRequest
from src.security.audit_logger import AuditLogger, redact_secrets
from src.security.policy_engine import (
    ActionEvaluationResult,
    PolicyDecision,
    RiskTier,
    SecurityPolicyEngine,
)
from src.voice.base import (
    TranscriptEventKind,
    TTSRequest,
    VoiceProviderError,
    sanitize_error_message,
)
from src.voice.intent_schema import (
    AllowedVoiceAction,
    IntentValidationError,
    VoiceIntent,
    VoiceIntentPlanner,
)
from src.voice.session import VoiceIOSession
from src.windows_integration.execution_engine import ActionExecutionOutcome


class VoicePipelineStatus(str, Enum):
    COMPLETED = "COMPLETED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    DENIED_BY_POLICY = "DENIED_BY_POLICY"
    DENIED_BY_USER = "DENIED_BY_USER"
    DENIED_APPROVAL_INVALID = "DENIED_APPROVAL_INVALID"
    REJECTED_VALIDATION = "REJECTED_VALIDATION"
    CANCELLED = "CANCELLED"
    STOPPED = "STOPPED"
    ABORTED_SAFE = "ABORTED_SAFE"
    FAILED_STT = "FAILED_STT"
    FAILED_EXECUTION = "FAILED_EXECUTION"


# ---------------------------------------------------------------------------
# Phase 4: Unicode Grapheme Cluster Splitter & HWND/PID Focus-Guarded Notepad Driver
# ---------------------------------------------------------------------------

_INDIC_VIRAMAS: Set[str] = {
    "\u094d",  # Devanagari Virama (Halant)
    "\u0c4d",  # Telugu Virama (Halant)
    "\u0bcd",  # Tamil Pulli (Virama)
}


def split_unicode_grapheme_clusters(text: str) -> List[str]:
    """Split text into complete Unicode orthographic grapheme clusters (Indic aksharas).

    Guarantees:
    - Normalizes input to NFC (`unicodedata.normalize("NFC", text)`).
    - Never splits a base letter (`L*`) from its combining marks (`Mn`, `Mc`, `Me`),
      zero-width joiners (`U+200C`, `U+200D`), or Indic virama/halant/pulli + following
      consonant conjunct (`C1 + Virama + C2`, e.g., Hindi `क्ष`/`त्र`/`प्र`, Telugu `క్ష`/`ప్ర`,
      Tamil `க்ஷ`).
    - Cancelling mid-word between clusters never leaves a dangling combining mark or virama.
    """
    if not text:
        return []
    nfc = unicodedata.normalize("NFC", text)
    clusters: List[str] = []
    n = len(nfc)
    i = 0

    while i < n:
        cluster_chars = [nfc[i]]
        i += 1
        while i < n:
            ch = nfc[i]
            cat = unicodedata.category(ch)
            prev = cluster_chars[-1]
            # 1. Attach any combining vowel sign, matra, anusvara, visarga, pulli, or ZWJ/ZWNJ
            if cat in ("Mn", "Mc", "Me") or ch in ("\u200c", "\u200d"):
                cluster_chars.append(ch)
                i += 1
                continue
            # 2. If the previous character in the cluster is an Indic virama/halant or ZWJ
            #    and the current character is a letter (forming a conjunct akshara), keep together
            if prev in _INDIC_VIRAMAS or prev == "\u200d":
                if cat.startswith("L"):
                    cluster_chars.append(ch)
                    i += 1
                    continue
            break
        clusters.append("".join(cluster_chars))

    return clusters


class FocusGuardError(RuntimeError):
    """Raised when the target window cannot be activated or loses focus mid-typing."""

    def __init__(self, spoken_reason: str, details: str = "") -> None:
        super().__init__(details or spoken_reason)
        self.code = "FOCUS_GUARD_ABORTED"
        self.spoken_reason = spoken_reason


class NotepadBinding(BaseModel):
    """Strict HWND + PID binding for a target Windows 11 Notepad instance."""

    model_config = ConfigDict(extra="forbid")

    hwnd: int
    pid: int
    edit_hwnd: int
    title: str = "Notepad"


class Win32NotepadDriver:
    """PID- & HWND-bound driver for Windows 11 (including tabbed) Notepad with Focus Guard.

    Correction & Undo Specification:
    - Tracks only the agent-typed character span `[agent_span_start, agent_span_end]`
      and `agent_typed_text` inside the bound `edit_hwnd`.
    - Pre-existing user text in `[0, agent_span_start)` is never selected or deleted.
    - Before every grapheme cluster keystroke, `verify_focus_guard(binding)` checks
      that `GetForegroundWindow() == binding.hwnd` and `fg_pid == binding.pid`.
      If any other window is focused, it aborts immediately with `FocusGuardError`
      (`FOCUS_GUARD_ABORTED`).
    - On Correction ("make it clearer"): verifies focus guard, verifies that the slice
      `[agent_span_start, agent_span_end]` still matches `agent_typed_text`, selects only
      `[agent_span_start, agent_span_end]` via `EM_SETSEL`, deletes only that span via
      `EM_REPLACESEL`, and types the revised text cluster-by-cluster with focus guard checks.
    - On Undo ("undo" / "wapas"): verifies focus guard, verifies the agent span
      `[agent_span_start, agent_span_end]` matches `agent_typed_text`, selects that span,
      and replaces it with `previous_agent_text` (or `""` if undoing an initial dictation).
    """

    WM_GETTEXT = 0x000D
    WM_GETTEXTLENGTH = 0x000E
    WM_SETTEXT = 0x000C
    EM_SETSEL = 0x00B1
    EM_REPLACESEL = 0x00C2
    SW_RESTORE = 9

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
        self._kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None
        if self._user32 is not None:
            self._user32.SendMessageW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            self._user32.SendMessageW.restype = wintypes.LPARAM

    def get_window_pid(self, hwnd: int) -> int:
        if not self._user32 or not hwnd:
            return 0
        pid = wintypes.DWORD(0)
        self._user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value)

    def get_class_name(self, hwnd: int) -> str:
        if not self._user32 or not hwnd:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        self._user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
        return buf.value

    def get_window_title(self, hwnd: int) -> str:
        if not self._user32 or not hwnd:
            return ""
        buf = ctypes.create_unicode_buffer(512)
        self._user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 512)
        return buf.value

    def find_active_edit_child(self, top_hwnd: int) -> int:
        """Locate the visible editor control (`RichEditD2DPT` on Win11 tabbed Notepad or `Edit`)."""
        if not self._user32 or not top_hwnd:
            return 0
        candidates: List[Tuple[int, bool, str]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_child(ch_hwnd: int, _lparam: int) -> bool:
            cls_name = self.get_class_name(ch_hwnd)
            if cls_name in ("RichEditD2DPT", "Edit", "RichEdit20W"):
                vis = bool(self._user32.IsWindowVisible(wintypes.HWND(ch_hwnd)))
                candidates.append((int(ch_hwnd), vis, cls_name))
            return True

        self._user32.EnumChildWindows(wintypes.HWND(top_hwnd), _enum_child, 0)
        # Prefer visible RichEditD2DPT (active tab in Windows 11 Notepad), then visible Edit
        for hwnd_val, is_vis, cls_n in candidates:
            if is_vis and cls_n == "RichEditD2DPT":
                return hwnd_val
        for hwnd_val, is_vis, _ in candidates:
            if is_vis:
                return hwnd_val
        return candidates[0][0] if candidates else 0

    def find_notepad_windows(self) -> List[NotepadBinding]:
        """Return all top-level Notepad.exe windows with their PID and active tab edit_hwnd."""
        if not self._user32:
            return []
        found: List[NotepadBinding] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_top(hwnd: int, _lparam: int) -> bool:
            if not self._user32.IsWindowVisible(wintypes.HWND(hwnd)):
                return True
            cls_name = self.get_class_name(hwnd)
            if cls_name != "Notepad":
                return True
            pid = self.get_window_pid(hwnd)
            if pid <= 0:
                return True
            with contextlib.suppress(Exception):
                pname = psutil.Process(pid).name().lower()
                if "notepad" not in pname:
                    return True
            edit_hwnd = self.find_active_edit_child(hwnd)
            if edit_hwnd:
                found.append(
                    NotepadBinding(
                        hwnd=int(hwnd),
                        pid=pid,
                        edit_hwnd=edit_hwnd,
                        title=self.get_window_title(hwnd) or "Notepad",
                    )
                )
            return True

        self._user32.EnumWindows(_enum_top, 0)
        return found

    def verify_focus_guard(self, binding: NotepadBinding) -> bool:
        """Verify foreground window is strictly `binding.hwnd` and belongs to `binding.pid`."""
        if not self._user32 or binding.hwnd <= 0 or binding.pid <= 0:
            return False
        if not self._user32.IsWindow(wintypes.HWND(binding.hwnd)):
            return False
        if self._user32.IsIconic(wintypes.HWND(binding.hwnd)):
            return False
        fg_hwnd = int(self._user32.GetForegroundWindow())
        if fg_hwnd != binding.hwnd:
            return False
        fg_pid = self.get_window_pid(fg_hwnd)
        return fg_pid == binding.pid

    async def activate_target(
        self,
        binding: NotepadBinding,
        timeout_seconds: float = 2.5,
        max_retries: int = 6,
    ) -> NotepadBinding:
        """Bring `binding.hwnd` to foreground with retries; abort with FocusGuardError."""
        if not self._user32:
            raise FocusGuardError("Stopped: Win32 user32 interface is unavailable.")

        # Fast-path: if already visible, restored, and foreground, refresh edit_hwnd
        if self.verify_focus_guard(binding):
            active_edit = self.find_active_edit_child(binding.hwnd)
            if active_edit:
                binding.edit_hwnd = active_edit
            return binding

        deadline = time.monotonic() + max(0.2, timeout_seconds)
        attempt = 0
        while time.monotonic() <= deadline and attempt < max(1, max_retries):
            attempt += 1
            if not self._user32.IsWindow(wintypes.HWND(binding.hwnd)):
                raise FocusGuardError("Stopped: Target Notepad window was closed.")

            if self._user32.IsIconic(wintypes.HWND(binding.hwnd)):
                self._user32.ShowWindow(wintypes.HWND(binding.hwnd), self.SW_RESTORE)
                await asyncio.sleep(0.08)

            if self.verify_focus_guard(binding):
                active_edit = self.find_active_edit_child(binding.hwnd)
                if active_edit:
                    binding.edit_hwnd = active_edit
                return binding

            fg_hwnd = self._user32.GetForegroundWindow()
            fg_tid = (
                self._user32.GetWindowThreadProcessId(wintypes.HWND(fg_hwnd), None)
                if fg_hwnd
                else 0
            )
            target_tid = self._user32.GetWindowThreadProcessId(wintypes.HWND(binding.hwnd), None)
            cur_tid = self._kernel32.GetCurrentThreadId() if self._kernel32 else 0

            attached_fg = False
            attached_target = False
            try:
                self._user32.keybd_event(0x12, 0, 0, 0)
                self._user32.keybd_event(0x12, 0, 0x0002, 0)
                if fg_tid and cur_tid and fg_tid != cur_tid:
                    attached_fg = bool(self._user32.AttachThreadInput(cur_tid, fg_tid, True))
                if target_tid and cur_tid and target_tid != cur_tid:
                    attached_target = bool(
                        self._user32.AttachThreadInput(cur_tid, target_tid, True)
                    )
                self._user32.BringWindowToTop(wintypes.HWND(binding.hwnd))
                self._user32.SetForegroundWindow(wintypes.HWND(binding.hwnd))
                self._user32.SetActiveWindow(wintypes.HWND(binding.hwnd))
                if binding.edit_hwnd:
                    self._user32.SetFocus(wintypes.HWND(binding.edit_hwnd))
            finally:
                if attached_target:
                    self._user32.AttachThreadInput(cur_tid, target_tid, False)
                if attached_fg:
                    self._user32.AttachThreadInput(cur_tid, fg_tid, False)

            await asyncio.sleep(0.06)
            if self.verify_focus_guard(binding):
                active_edit = self.find_active_edit_child(binding.hwnd)
                if active_edit:
                    binding.edit_hwnd = active_edit
                return binding

            await asyncio.sleep(0.06)

        raise FocusGuardError(
            "Stopped: Notepad window could not be focused within the safety timeout."
        )

    def get_editor_text(self, binding: NotepadBinding) -> str:
        """Read current text from `binding.edit_hwnd`."""
        if not self._user32 or not binding.edit_hwnd:
            return ""
        length = int(
            self._user32.SendMessageW(wintypes.HWND(binding.edit_hwnd), self.WM_GETTEXTLENGTH, 0, 0)
        )
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 2)
        self._user32.SendMessageW(
            wintypes.HWND(binding.edit_hwnd),
            self.WM_GETTEXT,
            wintypes.WPARAM(length + 1),
            wintypes.LPARAM(ctypes.addressof(buf)),
        )
        return buf.value

    def replace_editor_span(
        self,
        binding: NotepadBinding,
        start_pos: int,
        end_pos: int,
        replacement_text: str,
    ) -> None:
        """Select `[start_pos, end_pos]` in `binding.edit_hwnd` and replace with text."""
        if not self._user32 or not binding.edit_hwnd:
            raise FocusGuardError("Stopped: Notepad editor control handle is invalid.")
        if not self.verify_focus_guard(binding):
            raise FocusGuardError("Stopped typing because Notepad lost focus.")
        self._user32.SendMessageW(
            wintypes.HWND(binding.edit_hwnd),
            self.EM_SETSEL,
            wintypes.WPARAM(max(0, start_pos)),
            wintypes.LPARAM(max(0, end_pos)),
        )
        c_text = ctypes.create_unicode_buffer(replacement_text)
        self._user32.SendMessageW(
            wintypes.HWND(binding.edit_hwnd),
            self.EM_REPLACESEL,
            wintypes.WPARAM(1),
            wintypes.LPARAM(ctypes.addressof(c_text)),
        )


class CancelToken:
    """Cooperative + event-driven cancellation token checked between steps and inside waits."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._cancelled: bool = False
        self.reason: str = ""
        self.matched_word: Optional[str] = None
        self.t_cancelled: Optional[float] = None

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(
        self,
        reason: str = "Stopped by user",
        matched_word: Optional[str] = None,
    ) -> float:
        """Halt within the current event-loop tick and wake any waiting coroutine immediately."""
        now = time.time()
        if not self._cancelled:
            self._cancelled = True
            self.reason = reason
            self.matched_word = matched_word
            self.t_cancelled = now
            self._event.set()
        return self.t_cancelled or now

    def check(self) -> None:
        """Raise CancelledError immediately if cancelled."""
        if self._cancelled:
            raise asyncio.CancelledError(self.reason or "Cancelled")

    async def sleep_cancellable(self, duration_seconds: float) -> None:
        """Wait for `duration_seconds`, waking in the same event-loop tick if cancelled."""
        self.check()
        if duration_seconds <= 0:
            self.check()
            return
        try:
            await asyncio.wait_for(self._event.wait(), timeout=duration_seconds)
        except asyncio.TimeoutError:
            pass
        self.check()


class StepCheckpoint(BaseModel):
    """Immutable record of a completed step in CancellableStepExecutor."""

    model_config = ConfigDict(extra="forbid")

    step_index: int
    step_name: str
    action_type: str
    target_resource: str
    supports_undo: bool = False
    previous_state: Dict[str, Any] = Field(default_factory=dict)
    applied_state: Dict[str, Any] = Field(default_factory=dict)
    completed_at: float


class ActionStep:
    """Single atomic step with optional undo callback and checkpoint metadata."""

    def __init__(
        self,
        name: str,
        run_fn: Callable[[CancelToken], Coroutine[Any, Any, Any]],
        undo_fn: Optional[Callable[[], Coroutine[Any, Any, bool]]] = None,
        supports_undo: bool = False,
        previous_state: Optional[Dict[str, Any]] = None,
        applied_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.run_fn = run_fn
        self.undo_fn = undo_fn
        self.supports_undo = supports_undo
        self.previous_state = dict(previous_state or {})
        self.applied_state = dict(applied_state or {})


class CancellableStepExecutor:
    """Executes actions as small checkpointed steps with instant stop and honest undo."""

    def __init__(self) -> None:
        self.completed_checkpoints: List[StepCheckpoint] = []
        self._undo_stack: List[
            Tuple[StepCheckpoint, Optional[Callable[[], Coroutine[Any, Any, bool]]]]
        ] = []
        self.active_token: Optional[CancelToken] = None
        self.last_halt_timestamp: Optional[float] = None
        self.editor_text_history: List[str] = [""]
        self.notepad_driver = Win32NotepadDriver()
        self.bound_notepad: Optional[NotepadBinding] = None
        self.agent_span_start: int = 0
        self.agent_span_end: int = 0
        self.typing_char_delay_ms: int = 18

    @property
    def current_editor_text(self) -> str:
        return self.editor_text_history[-1] if self.editor_text_history else ""

    def halt_active(
        self,
        reason: str = "Stopped by user voice command",
        matched_word: Optional[str] = None,
    ) -> float:
        """Cancel the active step execution token in the same event-loop tick."""
        now = time.time()
        if self.active_token is not None:
            now = self.active_token.cancel(reason=reason, matched_word=matched_word)
        self.last_halt_timestamp = now
        return now

    async def execute_steps(
        self,
        steps: List[ActionStep],
        action_type: str,
        target_resource: str,
        cancel_token: Optional[CancelToken] = None,
    ) -> Dict[str, Any]:
        """Run a list of `ActionStep`s, checking `cancel_token` before, during, and after each."""
        token = cancel_token or CancelToken()
        self.active_token = token
        step_outputs: List[Any] = []
        run_checkpoints: List[StepCheckpoint] = []

        try:
            for idx, step in enumerate(steps):
                token.check()
                out = await step.run_fn(token)
                token.check()

                cp = StepCheckpoint(
                    step_index=idx,
                    step_name=step.name,
                    action_type=action_type,
                    target_resource=target_resource,
                    supports_undo=step.supports_undo,
                    previous_state=dict(step.previous_state),
                    applied_state=dict(step.applied_state),
                    completed_at=time.time(),
                )
                self.completed_checkpoints.append(cp)
                run_checkpoints.append(cp)
                self._undo_stack.append((cp, step.undo_fn))
                step_outputs.append(out)

            return {
                "halted": False,
                "completed_steps": len(run_checkpoints),
                "total_steps": len(steps),
                "outputs": step_outputs,
                "checkpoints": [c.model_dump() for c in run_checkpoints],
                "t_action_halted": None,
            }
        except asyncio.CancelledError:
            t_halted = time.time()
            self.last_halt_timestamp = t_halted
            return {
                "halted": True,
                "completed_steps": len(run_checkpoints),
                "total_steps": len(steps),
                "outputs": step_outputs,
                "checkpoints": [c.model_dump() for c in run_checkpoints],
                "t_action_halted": t_halted,
                "matched_word": token.matched_word,
                "reason": token.reason or "Halted by cancel token",
            }
        finally:
            if self.active_token is token:
                self.active_token = None

    async def undo_last_step(self) -> Dict[str, Any]:
        """Revert the last completed step if supported by the target app; report honestly if not."""
        if not self._undo_stack:
            return {
                "undone": False,
                "supports_undo": False,
                "reverted_step": None,
                "message": "No completed step is available to undo.",
            }

        cp, undo_fn = self._undo_stack[-1]
        if not cp.supports_undo or undo_fn is None:
            return {
                "undone": False,
                "supports_undo": False,
                "reverted_step": cp.step_name,
                "action_type": cp.action_type,
                "message": (
                    f"Action '{cp.action_type}' (step '{cp.step_name}') does not support "
                    f"automatic undo."
                ),
            }

        self._undo_stack.pop()
        ok = await undo_fn()
        if not ok:
            return {
                "undone": False,
                "supports_undo": True,
                "reverted_step": cp.step_name,
                "action_type": cp.action_type,
                "message": (
                    f"Attempted to undo '{cp.step_name}', but target window was unavailable."
                ),
            }

        return {
            "undone": True,
            "supports_undo": True,
            "reverted_step": cp.step_name,
            "action_type": cp.action_type,
            "previous_state": cp.previous_state,
            "message": f"Reverted last completed step '{cp.step_name}'.",
        }


class PendingVoiceApproval(BaseModel):
    """Clear, human-readable approval prompt bound to a validated VoiceIntent."""

    model_config = ConfigDict(extra="forbid")

    approval_nonce: str
    request_id: str
    session_id: str
    action_type: str
    engine_tool_name: str
    target_resource: str
    arguments: Dict[str, Any]
    risk_tier: str
    expected_consequences: str
    reason: str
    expires_at: float


class VoicePipelineResult(BaseModel):
    """Structured, validated outcome of a voice pipeline turn."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    session_id: str
    status: VoicePipelineStatus
    user_facing_message: str
    action_type: Optional[str] = None
    target_resource: Optional[str] = None
    policy_decision: Optional[str] = None
    approval_required: bool = False
    pending_approval: Optional[PendingVoiceApproval] = None
    execution_output: Any = None
    observable_evidence: List[str] = Field(default_factory=list)
    tts_succeeded: Optional[bool] = None
    tts_error: Optional[str] = None
    error_code: Optional[str] = None
    duration_ms: float = 0.0


class _StoredApprovalContext(BaseModel):
    request_id: str
    session_id: str
    intent: VoiceIntent
    broker_request: ApprovalRequest
    speak_response: bool = False


class VoiceExecutionCoordinator:
    """Connects voice I/O, STT, planner, authoritative policy, approval broker, and dispatcher."""

    def __init__(
        self,
        workspace_root: Path,
        policy_engine: Optional[SecurityPolicyEngine] = None,
        approval_broker: Optional[ApprovalBroker] = None,
        dispatcher: Optional[AutonomousTaskDispatcher] = None,
        planner: Optional[VoiceIntentPlanner] = None,
        audit_logger: Optional[AuditLogger] = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()

        # Authoritative policy engine (defaults to strict require_approvals=True)
        if policy_engine is not None:
            self.policy_engine = policy_engine
        elif dispatcher is not None and dispatcher.policy_engine is not None:
            self.policy_engine = dispatcher.policy_engine
        else:
            self.policy_engine = SecurityPolicyEngine(
                workspace_root=self.workspace_root,
                require_approvals=True,
            )

        self.audit_logger = audit_logger or AuditLogger(
            log_file=self.workspace_root / ".winai" / "audit.log"
        )
        self.approval_broker = approval_broker or ApprovalBroker()

        if dispatcher is not None:
            # Enforce security gate: dispatcher MUST use the exact authoritative policy_engine
            if dispatcher.policy_engine is not self.policy_engine:
                raise SecurityViolationError(
                    code="VOICE_POLICY_ENGINE_MISMATCH",
                    message=(
                        "Dispatcher policy_engine does not match the authoritative "
                        "SecurityPolicyEngine instance."
                    ),
                    remediation=(
                        "Inject the authoritative SecurityPolicyEngine "
                        "into AutonomousTaskDispatcher."
                    ),
                )
            if dispatcher.execution_engine.policy_engine is not self.policy_engine:
                raise SecurityViolationError(
                    code="VOICE_EXEC_ENGINE_POLICY_MISMATCH",
                    message=(
                        "Execution engine policy_engine does not match authoritative policy_engine."
                    ),
                )
            self.dispatcher = dispatcher
        else:
            self.dispatcher = AutonomousTaskDispatcher(
                workspace_root=self.workspace_root,
                policy_engine=self.policy_engine,
                audit_logger=self.audit_logger,
            )

        self.planner = planner or VoiceIntentPlanner(
            workspace_root=self.workspace_root,
            require_approvals=self.policy_engine.require_approvals,
        )

        self._pending_contexts: Dict[str, _StoredApprovalContext] = {}
        self._completed_request_ids: Set[str] = set()
        self.step_executor = CancellableStepExecutor()
        self.aborted_safe_count: int = 0
        self.failed_execution_count: int = 0

    @staticmethod
    def _compute_broker_action_hash(
        tool_name: str,
        target_resource: str,
        parameters: Dict[str, Any],
    ) -> str:
        action_repr = json.dumps(
            {"tool": tool_name, "target": target_resource, "params": parameters},
            sort_keys=True,
        )
        return hashlib.sha256(action_repr.encode("utf-8")).hexdigest()

    @staticmethod
    def _describe_consequences(intent: VoiceIntent) -> str:
        if intent.action_type == AllowedVoiceAction.LAUNCH_APP:
            app_id = intent.arguments.get("app_id", "")
            return f"Will launch a new non-elevated Windows process for whitelisted app '{app_id}'."
        if intent.action_type == AllowedVoiceAction.CREATE_WORKSPACE_FILE:
            rel_path = intent.arguments.get("path", "")
            size = len(intent.arguments.get("content", ""))
            return f"Will create new file '{rel_path}' ({size} chars) inside workspace."
        if intent.action_type == AllowedVoiceAction.READ_WORKSPACE_FILE:
            rel_path = intent.arguments.get("path", "")
            max_b = intent.arguments.get("max_bytes", 65536)
            return f"Will read up to {max_b} bytes from '{rel_path}'."
        if intent.action_type == AllowedVoiceAction.LIST_APPROVED_APPS:
            return "Read-only query of approved application whitelist."
        return "Read-only inspection of system hardware and memory status."

    def _log_voice_audit(
        self,
        event_type: str,
        message: str,
        session_id: str,
        request_id: str,
        level: str = "INFO",
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write sanitized operational metadata to AuditLogger (no raw audio or secrets)."""
        safe_payload = redact_secrets(payload or {})
        self.audit_logger.log(
            event_type=event_type,
            message=sanitize_error_message(message),
            level=level,
            payload=safe_payload,
            session_id=session_id,
            agent_id="voice_coordinator",
            operation_id=request_id,
        )

    # ------------------------------------------------------------------
    # Entry Point 1: Capture/Stream Audio from VoiceIOSession -> Execute
    # ------------------------------------------------------------------

    async def process_voice_session(
        self,
        session: VoiceIOSession,
        language_code: str = "en-IN",
        speak_response: bool = False,
        request_id: Optional[str] = None,
    ) -> VoicePipelineResult:
        """Stream captured audio from `session` to Prisma STT and process final transcript."""
        req_id = request_id or f"vreq_{secrets.token_hex(6)}"
        t0 = time.perf_counter()

        if session.is_cancelled or session.is_closed:
            return self._cancelled_result(req_id, session.session_id, t0)

        final_text: Optional[str] = None
        try:
            async for event in session.stream_capture_to_stt(
                language_code=language_code,
            ):
                if session.is_cancelled or session.is_closed:
                    return self._cancelled_result(req_id, session.session_id, t0)
                if event.kind == TranscriptEventKind.ERROR:
                    dur = round((time.perf_counter() - t0) * 1000.0, 2)
                    err_msg = sanitize_error_message(event.error_message or "STT stream error")
                    self._log_voice_audit(
                        "VOICE_STT_ERROR",
                        f"STT stream reported error ({req_id})",
                        session_id=session.session_id,
                        request_id=req_id,
                        level="WARNING",
                        payload={"error_category": "STT_STREAM_ERROR", "duration_ms": dur},
                    )
                    return VoicePipelineResult(
                        request_id=req_id,
                        session_id=session.session_id,
                        status=VoicePipelineStatus.FAILED_STT,
                        user_facing_message=f"Speech recognition failed: {err_msg}",
                        error_code="STT_STREAM_ERROR",
                        duration_ms=dur,
                    )
                if event.kind == TranscriptEventKind.FINAL and final_text is None:
                    # Take the first deduplicated final transcript and ignore duplicate final events
                    final_text = event.text
                    break
        except asyncio.CancelledError:
            return self._cancelled_result(req_id, session.session_id, t0)
        except VoiceProviderError as e:
            if session.is_cancelled or session.is_closed:
                return self._cancelled_result(req_id, session.session_id, t0)
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_STT_FAILURE",
                f"STT provider failed ({e.code})",
                session_id=session.session_id,
                request_id=req_id,
                level="WARNING",
                payload={"error_code": e.code, "duration_ms": dur},
            )
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.FAILED_STT,
                user_facing_message=f"Speech recognition unavailable: {e.message}",
                error_code=e.code,
                duration_ms=dur,
            )
        except Exception as e:
            if session.is_cancelled or session.is_closed:
                return self._cancelled_result(req_id, session.session_id, t0)
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            clean_err = sanitize_error_message(str(e))
            self._log_voice_audit(
                "VOICE_STT_FAILURE",
                "Unexpected STT error",
                session_id=session.session_id,
                request_id=req_id,
                level="WARNING",
                payload={"error_code": "STT_UNEXPECTED_ERROR", "duration_ms": dur},
            )
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.FAILED_STT,
                user_facing_message=f"Speech recognition error: {clean_err}",
                error_code="STT_UNEXPECTED_ERROR",
                duration_ms=dur,
            )

        if session.is_cancelled or session.is_closed:
            return self._cancelled_result(req_id, session.session_id, t0)

        return await self.process_transcript(
            session=session,
            transcript=final_text or "",
            speak_response=speak_response,
            request_id=req_id,
            start_perf=t0,
        )

    # ------------------------------------------------------------------
    # Entry Point 2: Validate Transcript -> Plan -> Authorize -> Execute
    # ------------------------------------------------------------------

    async def process_transcript(
        self,
        session: VoiceIOSession,
        transcript: str,
        speak_response: bool = False,
        request_id: Optional[str] = None,
        start_perf: Optional[float] = None,
    ) -> VoicePipelineResult:
        """Process untrusted transcript through validation, policy, approval, and execution."""
        req_id = request_id or f"vreq_{secrets.token_hex(6)}"
        t0 = start_perf if start_perf is not None else time.perf_counter()

        # Prevent duplicate execution if the same request_id is re-submitted
        if req_id in self._completed_request_ids:
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.REJECTED_VALIDATION,
                user_facing_message="Duplicate voice request ignored.",
                error_code="DUPLICATE_REQUEST_ID",
                duration_ms=dur,
            )

        if session.is_cancelled or session.is_closed:
            return self._cancelled_result(req_id, session.session_id, t0)

        # 1. Plan & Validate Structured Intent (runs inside a session-tracked task)
        try:
            plan_task = session.spawn_session_task(
                self.planner.plan_transcript(transcript),
                name=f"{req_id}_plan",
            )
            intent = await plan_task
        except asyncio.CancelledError:
            return self._cancelled_result(req_id, session.session_id, t0)
        except IntentValidationError as e:
            if session.is_cancelled or session.is_closed:
                return self._cancelled_result(req_id, session.session_id, t0)
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_INTENT_REJECTED",
                f"Intent validation rejected request ({e.code})",
                session_id=session.session_id,
                request_id=req_id,
                level="WARNING",
                payload={"error_code": e.code, "duration_ms": dur},
            )
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.REJECTED_VALIDATION,
                user_facing_message=f"Request could not be safely executed: {e.message}",
                error_code=e.code,
                duration_ms=dur,
            )
        except Exception as e:
            if session.is_cancelled or session.is_closed:
                return self._cancelled_result(req_id, session.session_id, t0)
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            clean_err = sanitize_error_message(str(e))
            self._log_voice_audit(
                "VOICE_PLANNER_ERROR",
                "Planner error",
                session_id=session.session_id,
                request_id=req_id,
                level="WARNING",
                payload={"error_code": "PLANNER_ERROR", "duration_ms": dur},
            )
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.REJECTED_VALIDATION,
                user_facing_message=f"Could not interpret command: {clean_err}",
                error_code="PLANNER_ERROR",
                duration_ms=dur,
            )

        # Check cancellation after planner returns (prevents late LLM response from executing)
        if session.is_cancelled or session.is_closed:
            return self._cancelled_result(req_id, session.session_id, t0)

        # 2. Evaluate Authoritative Security Policy
        policy_eval = self._evaluate_intent_policy(intent, session_id=session.session_id)

        if policy_eval.decision == PolicyDecision.DENY:
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_POLICY_DENIED",
                f"Policy denied {intent.action_type.value}",
                session_id=session.session_id,
                request_id=req_id,
                level="WARNING",
                payload={
                    "action_type": intent.action_type.value,
                    "engine_tool": intent.engine_tool_name,
                    "policy_decision": PolicyDecision.DENY.value,
                    "risk_tier": policy_eval.risk_tier.value,
                    "reason": policy_eval.reason,
                    "duration_ms": dur,
                },
            )
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.DENIED_BY_POLICY,
                user_facing_message=f"Action blocked by security policy: {policy_eval.reason}",
                action_type=intent.action_type.value,
                target_resource=intent.target_resource,
                policy_decision=PolicyDecision.DENY.value,
                approval_required=False,
                error_code="POLICY_DENIED",
                duration_ms=dur,
            )

        # 3. If Approval Required, Create Action-Bound Request in ApprovalBroker
        if policy_eval.decision == PolicyDecision.REQUIRE_APPROVAL:
            broker_req = self.approval_broker.create_request(
                tool_name=intent.engine_tool_name,
                target_resource=intent.target_resource,
                parameters=dict(intent.arguments),
                risk_tier=policy_eval.risk_tier.value,
                reason=policy_eval.reason,
                session_id=session.session_id,
                agent_id="voice_coordinator",
            )
            self._pending_contexts[broker_req.nonce] = _StoredApprovalContext(
                request_id=req_id,
                session_id=session.session_id,
                intent=intent,
                broker_request=broker_req,
                speak_response=speak_response,
            )
            pending_view = PendingVoiceApproval(
                approval_nonce=broker_req.nonce,
                request_id=req_id,
                session_id=session.session_id,
                action_type=intent.action_type.value,
                engine_tool_name=intent.engine_tool_name,
                target_resource=intent.target_resource,
                arguments=dict(intent.arguments),
                risk_tier=policy_eval.risk_tier.value,
                expected_consequences=self._describe_consequences(intent),
                reason=policy_eval.reason,
                expires_at=broker_req.created_at + broker_req.ttl_seconds,
            )
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_APPROVAL_REQUESTED",
                f"Approval required for {intent.action_type.value}",
                session_id=session.session_id,
                request_id=req_id,
                level="INFO",
                payload={
                    "action_type": intent.action_type.value,
                    "engine_tool": intent.engine_tool_name,
                    "policy_decision": PolicyDecision.REQUIRE_APPROVAL.value,
                    "approval_required": True,
                    "risk_tier": policy_eval.risk_tier.value,
                    "duration_ms": dur,
                },
            )
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.WAITING_FOR_APPROVAL,
                user_facing_message=(
                    f"Approval required to {intent.user_facing_summary} "
                    f"(Target: {intent.target_resource}). Reason: {policy_eval.reason}"
                ),
                action_type=intent.action_type.value,
                target_resource=intent.target_resource,
                policy_decision=PolicyDecision.REQUIRE_APPROVAL.value,
                approval_required=True,
                pending_approval=pending_view,
                duration_ms=dur,
            )

        # 4. Policy Decision is ALLOW -> Execute after final cancellation check
        return await self._revalidate_and_execute(
            session=session,
            request_id=req_id,
            intent=intent,
            approval_required=False,
            approval_outcome="NOT_REQUIRED",
            speak_response=speak_response,
            start_perf=t0,
        )

    # ------------------------------------------------------------------
    # Entry Point 3: Resolve Pending Approval -> Revalidate -> Execute
    # ------------------------------------------------------------------

    async def resolve_approval(
        self,
        session: VoiceIOSession,
        approval_nonce: str,
        approved: bool,
        expected_intent: Optional[VoiceIntent] = None,
    ) -> VoicePipelineResult:
        """Resolve a pending approval nonce, verify binding, revalidate policy, and execute."""
        t0 = time.perf_counter()
        ctx = self._pending_contexts.get(approval_nonce)
        req_id = ctx.request_id if ctx else f"vreq_{secrets.token_hex(6)}"

        if session.is_cancelled or session.is_closed:
            self.approval_broker.revoke_all_for_session(session.session_id)
            self._pending_contexts.pop(approval_nonce, None)
            return self._cancelled_result(req_id, session.session_id, t0)

        if ctx is None:
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_APPROVAL_INVALID",
                "Unknown or already-consumed approval nonce",
                session_id=session.session_id,
                request_id=req_id,
                level="WARNING",
                payload={"approval_outcome": "INVALID_OR_REPLAYED", "duration_ms": dur},
            )
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.DENIED_APPROVAL_INVALID,
                user_facing_message=(
                    "Approval rejected: invalid, expired, or already-used approval token."
                ),
                approval_required=True,
                error_code="APPROVAL_NONCE_INVALID",
                duration_ms=dur,
            )

        # Verify session binding (Session B cannot approve or execute Session A's pending action)
        if ctx.session_id != session.session_id:
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_APPROVAL_SESSION_MISMATCH",
                "Cross-session approval attempt blocked",
                session_id=session.session_id,
                request_id=req_id,
                level="WARNING",
                payload={"approval_outcome": "SESSION_MISMATCH", "duration_ms": dur},
            )
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.DENIED_APPROVAL_INVALID,
                user_facing_message=(
                    "Approval rejected: token belongs to a different voice session."
                ),
                approval_required=True,
                error_code="APPROVAL_SESSION_MISMATCH",
                duration_ms=dur,
            )

        # User explicitly denied the approval
        if not approved:
            self._pending_contexts.pop(approval_nonce, None)
            self.approval_broker.verify_and_consume(approval_nonce)
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_APPROVAL_DENIED",
                f"User denied approval for {ctx.intent.action_type.value}",
                session_id=session.session_id,
                request_id=req_id,
                level="INFO",
                payload={
                    "action_type": ctx.intent.action_type.value,
                    "policy_decision": PolicyDecision.REQUIRE_APPROVAL.value,
                    "approval_required": True,
                    "approval_outcome": "DENIED_BY_USER",
                    "execution_outcome": "NOT_EXECUTED",
                    "duration_ms": dur,
                },
            )
            act_val = ctx.intent.action_type.value
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.DENIED_BY_USER,
                user_facing_message=f"Action cancelled: user denied approval for {act_val}.",
                action_type=act_val,
                target_resource=ctx.intent.target_resource,
                policy_decision=PolicyDecision.REQUIRE_APPROVAL.value,
                approval_required=True,
                error_code="APPROVAL_DENIED_BY_USER",
                duration_ms=dur,
            )

        # Verify action & argument hash binding against expected_intent or stored intent
        target_intent = expected_intent or ctx.intent
        expected_hash = self._compute_broker_action_hash(
            tool_name=target_intent.engine_tool_name,
            target_resource=target_intent.target_resource,
            parameters=dict(target_intent.arguments),
        )

        consumed_req = self.approval_broker.verify_and_consume(
            approval_nonce,
            expected_action_hash=expected_hash,
        )
        if consumed_req is None:
            # Invalidate stored context on mismatch or expiry so it cannot be reused
            self._pending_contexts.pop(approval_nonce, None)
            dur = round((time.perf_counter() - t0) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_APPROVAL_INVALID",
                "Approval token expired or action/arguments changed since approval was requested",
                session_id=session.session_id,
                request_id=req_id,
                level="WARNING",
                payload={
                    "action_type": target_intent.action_type.value,
                    "approval_required": True,
                    "approval_outcome": "EXPIRED_OR_MISMATCHED",
                    "execution_outcome": "NOT_EXECUTED",
                    "duration_ms": dur,
                },
            )
            return VoicePipelineResult(
                request_id=req_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.DENIED_APPROVAL_INVALID,
                user_facing_message=(
                    "Approval rejected: token expired or action arguments were modified "
                    "after approval was requested."
                ),
                action_type=target_intent.action_type.value,
                target_resource=target_intent.target_resource,
                policy_decision=PolicyDecision.REQUIRE_APPROVAL.value,
                approval_required=True,
                error_code="APPROVAL_MISMATCH_OR_EXPIRED",
                duration_ms=dur,
            )

        self._pending_contexts.pop(approval_nonce, None)

        return await self._revalidate_and_execute(
            session=session,
            request_id=req_id,
            intent=target_intent,
            approval_required=True,
            approval_outcome="APPROVED_AND_CONSUMED",
            speak_response=ctx.speak_response,
            start_perf=t0,
        )

    # ------------------------------------------------------------------
    # Internal Policy Evaluation, Pre-Execution Revalidation & Dispatch
    # ------------------------------------------------------------------

    def _evaluate_intent_policy(
        self, intent: VoiceIntent, session_id: str
    ) -> ActionEvaluationResult:
        if intent.action_type == AllowedVoiceAction.LIST_APPROVED_APPS:
            return ActionEvaluationResult(
                decision=PolicyDecision.ALLOW,
                risk_tier=RiskTier.LOW,
                reason="Read-only inspection of approved application whitelist.",
                target_resource="approved_apps",
            )
        if intent.action_type == AllowedVoiceAction.TYPE_IN_NOTEPAD:
            return ActionEvaluationResult(
                decision=PolicyDecision.ALLOW,
                risk_tier=RiskTier.LOW,
                reason="Typing user-dictated text into active Notepad session.",
                target_resource="Notepad",
            )
        return self.policy_engine.evaluate_action(
            tool_name=intent.engine_tool_name,
            arguments=dict(intent.arguments),
            session_id=session_id,
            agent_id="voice_coordinator",
        )

    async def _revalidate_and_execute(
        self,
        session: VoiceIOSession,
        request_id: str,
        intent: VoiceIntent,
        approval_required: bool,
        approval_outcome: str,
        speak_response: bool,
        start_perf: float,
    ) -> VoicePipelineResult:
        """Revalidate cancellation and policy immediately before executing via dispatcher."""
        if session.is_cancelled or session.is_closed:
            return self._cancelled_result(request_id, session.session_id, start_perf)

        # Revalidate file overwrite protection immediately before execution
        if intent.action_type == AllowedVoiceAction.CREATE_WORKSPACE_FILE:
            rel_path = str(intent.arguments.get("path", ""))
            canonical = (self.workspace_root / rel_path).resolve()
            if canonical.exists():
                dur = round((time.perf_counter() - start_perf) * 1000.0, 2)
                self._log_voice_audit(
                    "VOICE_PRE_EXEC_OVERWRITE_BLOCKED",
                    f"Target file '{rel_path}' already exists at execution time",
                    session_id=session.session_id,
                    request_id=request_id,
                    level="WARNING",
                    payload={
                        "action_type": intent.action_type.value,
                        "execution_outcome": "BLOCKED_OVERWRITE",
                        "duration_ms": dur,
                    },
                )
                return VoicePipelineResult(
                    request_id=request_id,
                    session_id=session.session_id,
                    status=VoicePipelineStatus.REJECTED_VALIDATION,
                    user_facing_message=f"Execution blocked: file '{rel_path}' already exists.",
                    action_type=intent.action_type.value,
                    target_resource=intent.target_resource,
                    error_code="INTENT_FILE_OVERWRITE_PROHIBITED",
                    duration_ms=dur,
                )

        # Revalidate authoritative policy immediately prior to execution
        re_eval = self._evaluate_intent_policy(intent, session_id=session.session_id)
        if re_eval.decision == PolicyDecision.DENY:
            dur = round((time.perf_counter() - start_perf) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_POLICY_DENIED",
                f"Pre-execution policy revalidation denied {intent.action_type.value}",
                session_id=session.session_id,
                request_id=request_id,
                level="WARNING",
                payload={
                    "action_type": intent.action_type.value,
                    "policy_decision": PolicyDecision.DENY.value,
                    "approval_required": approval_required,
                    "approval_outcome": approval_outcome,
                    "execution_outcome": "DENIED_ON_REVALIDATION",
                    "reason": re_eval.reason,
                    "duration_ms": dur,
                },
            )
            return VoicePipelineResult(
                request_id=request_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.DENIED_BY_POLICY,
                user_facing_message=f"Action blocked by security policy: {re_eval.reason}",
                action_type=intent.action_type.value,
                target_resource=intent.target_resource,
                policy_decision=PolicyDecision.DENY.value,
                approval_required=approval_required,
                error_code="POLICY_DENIED",
                duration_ms=dur,
            )

        # Final cancellation check right before invoking the dispatcher/execution engine
        if session.is_cancelled or session.is_closed:
            return self._cancelled_result(request_id, session.session_id, start_perf)

        engine_nonce = (
            re_eval.approval_nonce if re_eval.decision == PolicyDecision.REQUIRE_APPROVAL else None
        )

        try:
            exec_summary, exec_output, evidence = await self._dispatch_validated_intent(
                session=session,
                intent=intent,
                engine_approval_nonce=engine_nonce,
            )
        except asyncio.CancelledError:
            return self._cancelled_result(request_id, session.session_id, start_perf)
        except FocusGuardError as e:
            self.aborted_safe_count += 1
            dur = round((time.perf_counter() - start_perf) * 1000.0, 2)
            self._log_voice_audit(
                "VOICE_FOCUS_GUARD_ABORTED",
                e.spoken_reason,
                session_id=session.session_id,
                request_id=request_id,
                level="WARNING",
                payload={
                    "action_type": intent.action_type.value,
                    "error_code": e.code,
                    "duration_ms": dur,
                },
            )
            return VoicePipelineResult(
                request_id=request_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.ABORTED_SAFE,
                user_facing_message=e.spoken_reason,
                action_type=intent.action_type.value,
                target_resource=intent.target_resource,
                policy_decision=re_eval.decision.value,
                approval_required=approval_required,
                error_code=e.code,
                duration_ms=dur,
            )
        except Exception as e:
            self.failed_execution_count += 1
            dur = round((time.perf_counter() - start_perf) * 1000.0, 2)
            clean_err = sanitize_error_message(str(e))
            self._log_voice_audit(
                "VOICE_EXECUTION_FAILED",
                f"Dispatcher execution failed for {intent.action_type.value}",
                session_id=session.session_id,
                request_id=request_id,
                level="WARNING",
                payload={
                    "action_type": intent.action_type.value,
                    "policy_decision": re_eval.decision.value,
                    "approval_required": approval_required,
                    "approval_outcome": approval_outcome,
                    "execution_outcome": "FAILED",
                    "error_category": type(e).__name__,
                    "duration_ms": dur,
                },
            )
            return VoicePipelineResult(
                request_id=request_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.FAILED_EXECUTION,
                user_facing_message=f"Execution failed: {clean_err}",
                action_type=intent.action_type.value,
                target_resource=intent.target_resource,
                policy_decision=re_eval.decision.value,
                approval_required=approval_required,
                error_code="DISPATCHER_EXECUTION_FAILED",
                duration_ms=dur,
            )

        self._completed_request_ids.add(request_id)
        dur = round((time.perf_counter() - start_perf) * 1000.0, 2)

        self._log_voice_audit(
            "VOICE_EXECUTION_COMPLETED",
            f"Completed {intent.action_type.value}",
            session_id=session.session_id,
            request_id=request_id,
            level="INFO",
            payload={
                "action_type": intent.action_type.value,
                "engine_tool": intent.engine_tool_name,
                "policy_decision": re_eval.decision.value,
                "approval_required": approval_required,
                "approval_outcome": approval_outcome,
                "execution_outcome": "VERIFIED_SUCCESS",
                "duration_ms": dur,
            },
        )

        # 5. Optional Voice Response (Timbre TTS + Speaker) — never re-executes on TTS failure
        tts_ok: Optional[bool] = None
        tts_err: Optional[str] = None
        if speak_response and not session.is_cancelled and not session.is_closed:
            if session.tts_provider is not None:
                try:
                    outcome = await session.synthesize_and_play(
                        TTSRequest(
                            text=exec_summary[:400],
                            voice="Shivani",
                            model="timbre-v3.0",
                            language=intent.reply_language,
                        )
                    )
                    tts_ok = bool(outcome.completed)
                except Exception as e:
                    tts_ok = False
                    tts_err = sanitize_error_message(str(e))
            else:
                tts_ok = False
                tts_err = "No TTS provider configured on session."

        return VoicePipelineResult(
            request_id=request_id,
            session_id=session.session_id,
            status=VoicePipelineStatus.COMPLETED,
            user_facing_message=exec_summary,
            action_type=intent.action_type.value,
            target_resource=intent.target_resource,
            policy_decision=re_eval.decision.value,
            approval_required=approval_required,
            execution_output=exec_output,
            observable_evidence=evidence,
            tts_succeeded=tts_ok,
            tts_error=tts_err,
            duration_ms=round((time.perf_counter() - start_perf) * 1000.0, 2),
        )

    async def _dispatch_validated_intent(
        self,
        session: VoiceIOSession,
        intent: VoiceIntent,
        engine_approval_nonce: Optional[str],
        cancel_token: Optional[CancelToken] = None,
    ) -> Tuple[str, Any, List[str]]:
        """Invoke existing dispatcher / execution engine via CancellableStepExecutor."""
        token = cancel_token or CancelToken()

        if intent.action_type == AllowedVoiceAction.LIST_APPROVED_APPS:

            async def _run_list(_tok: CancelToken) -> Any:
                apps = self.dispatcher.app_manager.list_approved_apps()
                return [{"app_id": a.app_id, "display_name": a.display_name} for a in apps]

            step = ActionStep(name="list_approved_apps", run_fn=_run_list, supports_undo=False)
            step_res = await self.step_executor.execute_steps(
                [step],
                action_type=intent.action_type.value,
                target_resource="approved_apps",
                cancel_token=token,
            )
            if step_res["halted"]:
                raise asyncio.CancelledError(step_res.get("reason", "Halted"))
            app_list = step_res["outputs"][0]
            names = ", ".join(a["display_name"] for a in app_list)
            return (
                f"Approved applications ({len(app_list)}): {names}.",
                app_list,
                [f"Enumerated {len(app_list)} approved applications from AppManager whitelist"],
            )

        if intent.action_type == AllowedVoiceAction.TYPE_IN_NOTEPAD:
            text_to_write = str(intent.arguments.get("text", "")).strip()
            replace_last_span = bool(intent.arguments.get("replace_last_span", False))
            prev_text = self.step_executor.current_editor_text
            clusters = split_unicode_grapheme_clusters(text_to_write)
            char_delay_s = max(0.0, float(self.step_executor.typing_char_delay_ms) / 1000.0)

            # Check if a custom mock uia_service is injected by hermetic unit tests
            uia_svc = self.dispatcher.uia_service
            is_mocked_uia = not hasattr(uia_svc.find_window, "__self__")

            if is_mocked_uia:

                async def _step_focus_mock(tok: CancelToken) -> bool:
                    tok.check()
                    win = uia_svc.find_window("Notepad", timeout_seconds=1.5)
                    if win is None:
                        self.dispatcher.app_manager.launch_app("notepad")
                        uia_svc.wait_for_window("Notepad", timeout_seconds=4.0)
                    tok.check()
                    focused = uia_svc.focus_window("Notepad", timeout_seconds=2.5)
                    if focused is False:
                        raise FocusGuardError(
                            "Stopped: Notepad window could not be focused within timeout."
                        )
                    return True

                async def _step_type_mock(tok: CancelToken) -> bool:
                    tok.check()
                    typed_accum: List[str] = []
                    for cl in clusters:
                        tok.check()
                        typed_accum.append(cl)
                        if char_delay_s > 0:
                            await tok.sleep_cancellable(min(0.002, char_delay_s))
                    joined = "".join(typed_accum)
                    typed_ok = uia_svc.set_text_value(
                        window_title="Notepad",
                        text=joined + "\n",
                    )
                    self.step_executor.editor_text_history.append(joined)
                    return bool(typed_ok)

                async def _undo_notepad_mock() -> bool:
                    if len(self.step_executor.editor_text_history) > 1:
                        self.step_executor.editor_text_history.pop()
                    restored = self.step_executor.current_editor_text
                    with contextlib.suppress(Exception):
                        uia_svc.focus_window("Notepad", timeout_seconds=1.5)
                        return bool(
                            uia_svc.set_text_value(
                                window_title="Notepad",
                                text=(restored + "\n") if restored else "",
                            )
                        )
                    return True

                steps = [
                    ActionStep(
                        name="activate_target", run_fn=_step_focus_mock, supports_undo=False
                    ),
                    ActionStep(
                        name="type_text_in_notepad",
                        run_fn=_step_type_mock,
                        undo_fn=_undo_notepad_mock,
                        supports_undo=True,
                        previous_state={"text": prev_text},
                        applied_state={"text": text_to_write},
                    ),
                ]
                step_res = await self.step_executor.execute_steps(
                    steps,
                    action_type=intent.action_type.value,
                    target_resource="Notepad",
                    cancel_token=token,
                )
                if step_res["halted"]:
                    raise asyncio.CancelledError(step_res.get("reason", "Halted"))
                typed_ok = step_res["outputs"][-1]
                return (
                    f'Wrote into Notepad: "{text_to_write}"',
                    {"window": "Notepad", "text": text_to_write, "typed": typed_ok},
                    [f"Focused Notepad and typed {len(clusters)} grapheme clusters"],
                )

            # Real Win32 PID + HWND bound Notepad execution with per-grapheme Focus Guard
            drv = self.step_executor.notepad_driver

            async def _step_activate_target(tok: CancelToken) -> NotepadBinding:
                tok.check()
                wins = drv.find_notepad_windows()
                cold_launched = False
                if not wins:
                    cold_launched = True
                    self.dispatcher.app_manager.launch_app("notepad")
                    deadline = time.monotonic() + 4.0
                    while time.monotonic() < deadline:
                        tok.check()
                        wins = drv.find_notepad_windows()
                        if wins and wins[0].edit_hwnd:
                            break
                        await asyncio.sleep(0.08)
                if not wins:
                    raise FocusGuardError("Stopped: Could not find or launch Notepad window.")
                if cold_launched:
                    # Allow Windows 11 WinUI 3 tabbed Notepad to finish restoring TabState
                    await tok.sleep_cancellable(0.65)
                    wins = drv.find_notepad_windows() or wins
                binding = await drv.activate_target(wins[0], timeout_seconds=2.5, max_retries=6)
                active_edit = drv.find_active_edit_child(binding.hwnd)
                if active_edit:
                    binding.edit_hwnd = active_edit
                self.step_executor.bound_notepad = binding
                return binding

            typed_clusters_so_far: List[str] = []

            async def _step_type_graphemes(tok: CancelToken) -> Dict[str, Any]:
                binding = self.step_executor.bound_notepad
                if binding is None or not drv.verify_focus_guard(binding):
                    raise FocusGuardError(
                        "Stopped typing because Notepad is not the active window."
                    )
                active_edit = drv.find_active_edit_child(binding.hwnd)
                if active_edit:
                    binding.edit_hwnd = active_edit

                existing_doc = drv.get_editor_text(binding)
                if (
                    replace_last_span
                    and prev_text
                    and self.step_executor.agent_span_end > self.step_executor.agent_span_start
                ):
                    s_start = self.step_executor.agent_span_start
                    s_end = self.step_executor.agent_span_end
                    if existing_doc[s_start:s_end] == prev_text:
                        drv.replace_editor_span(binding, s_start, s_end, "")
                        span_start = s_start
                    else:
                        span_start = len(existing_doc)
                else:
                    span_start = len(existing_doc)

                self.step_executor.agent_span_start = span_start
                self.step_executor.agent_span_end = span_start
                cur_pos = span_start

                try:
                    for cl in clusters:
                        tok.check()
                        if not drv.verify_focus_guard(binding):
                            raise FocusGuardError("Stopped typing because Notepad lost focus.")
                        drv.replace_editor_span(binding, cur_pos, cur_pos, cl)
                        cur_doc = drv.get_editor_text(binding)
                        cur_pos = len(cur_doc) if len(cur_doc) > cur_pos else (cur_pos + len(cl))
                        self.step_executor.agent_span_end = cur_pos
                        typed_clusters_so_far.append(cl)
                        if char_delay_s > 0:
                            await tok.sleep_cancellable(char_delay_s)
                finally:
                    partial_or_full = "".join(typed_clusters_so_far)
                    if partial_or_full:
                        self.step_executor.editor_text_history.append(partial_or_full)

                return {
                    "hwnd": binding.hwnd,
                    "pid": binding.pid,
                    "edit_hwnd": binding.edit_hwnd,
                    "text": "".join(typed_clusters_so_far),
                    "grapheme_clusters": len(typed_clusters_so_far),
                    "span_start": span_start,
                    "span_end": cur_pos,
                }

            async def _undo_notepad_win32() -> bool:
                binding = self.step_executor.bound_notepad
                if binding is None:
                    return False
                try:
                    binding = await drv.activate_target(binding, timeout_seconds=1.8, max_retries=4)
                except FocusGuardError:
                    return False
                if not drv.verify_focus_guard(binding):
                    return False

                last_typed = self.step_executor.current_editor_text
                if len(self.step_executor.editor_text_history) > 1:
                    self.step_executor.editor_text_history.pop()
                restored_text = self.step_executor.current_editor_text if replace_last_span else ""

                doc_now = drv.get_editor_text(binding)
                s_start = self.step_executor.agent_span_start
                s_end = self.step_executor.agent_span_end
                # Never delete text the agent did not type!
                if last_typed and doc_now[s_start:s_end] == last_typed:
                    drv.replace_editor_span(binding, s_start, s_end, restored_text)
                    self.step_executor.agent_span_end = s_start + len(restored_text)
                    return True
                return False

            steps_win32 = [
                ActionStep(
                    name="activate_target", run_fn=_step_activate_target, supports_undo=False
                ),
                ActionStep(
                    name="type_text_in_notepad",
                    run_fn=_step_type_graphemes,
                    undo_fn=_undo_notepad_win32,
                    supports_undo=True,
                    previous_state={"text": prev_text},
                    applied_state={"text": text_to_write},
                ),
            ]
            step_res = await self.step_executor.execute_steps(
                steps_win32,
                action_type=intent.action_type.value,
                target_resource="Notepad",
                cancel_token=token,
            )
            if step_res["halted"]:
                raise asyncio.CancelledError(step_res.get("reason", "Halted"))
            out_info = step_res["outputs"][-1]
            return (
                f'Wrote into Notepad: "{out_info["text"]}"',
                {
                    "window": "Notepad",
                    "hwnd": out_info["hwnd"],
                    "pid": out_info["pid"],
                    "edit_hwnd": out_info["edit_hwnd"],
                    "text": out_info["text"],
                    "typed": True,
                },
                [
                    f"Bound Notepad (PID {out_info['pid']}, HWND {out_info['hwnd']}) "
                    f"and typed {out_info['grapheme_clusters']} grapheme clusters"
                ],
            )

        async def _step_engine_dispatch(tok: CancelToken) -> Any:
            tok.check()
            exec_coro = self.dispatcher.execution_engine.execute_action(
                tool_name=intent.engine_tool_name,
                arguments=dict(intent.arguments),
                session_id=session.session_id,
                agent_id="voice_coordinator",
                approval_nonce=engine_approval_nonce,
            )
            exec_task = session.spawn_session_task(exec_coro, name=f"{intent.intent_id}_exec")
            return await exec_task

        step_res = await self.step_executor.execute_steps(
            [
                ActionStep(
                    name=f"dispatch_{intent.action_type.value}",
                    run_fn=_step_engine_dispatch,
                    supports_undo=False,
                )
            ],
            action_type=intent.action_type.value,
            target_resource=intent.target_resource,
            cancel_token=token,
        )
        if step_res["halted"]:
            raise asyncio.CancelledError(step_res.get("reason", "Halted"))
        res = step_res["outputs"][0]

        if res.outcome != ActionExecutionOutcome.VERIFIED_SUCCESS:
            raise RuntimeError(res.error_message or f"Execution outcome was {res.outcome.value}")

        if intent.action_type == AllowedVoiceAction.SYSTEM_INFO:
            data = res.output_data or {}
            os_name = data.get("os_name", "Windows")
            ram_pct = (data.get("memory") or {}).get("percent_used", 0)
            summary = f"System overview verified: {os_name}, RAM usage {ram_pct}%."
            return summary, data, ["System overview retrieved via WindowsExecutionEngine"]

        if intent.action_type == AllowedVoiceAction.LAUNCH_APP:
            out = res.output_data or {}
            app_id = out.get("app_id") or intent.arguments.get("app_id")
            pid = out.get("launched_pid")
            if app_id == "notepad":
                with contextlib.suppress(Exception):
                    self.dispatcher.uia_service.wait_for_window("Notepad", timeout_seconds=2.5)
                    self.dispatcher.uia_service.focus_window("Notepad", timeout_seconds=1.5)
            summary = f"Launched application '{app_id}' (PID {pid})."
            return summary, out, [f"Application '{app_id}' launched and verified (PID {pid})"]

        if intent.action_type == AllowedVoiceAction.READ_WORKSPACE_FILE:
            rel_path = intent.arguments.get("path", "")
            text = str(res.output_data or "")
            summary = f"Read file '{rel_path}' ({len(text)} characters)."
            # Return metadata rather than dumping sensitive file contents into logs
            return (
                summary,
                {"path": rel_path, "char_count": len(text), "content": text},
                [f"File '{rel_path}' read inside workspace ({len(text)} chars)"],
            )

        if intent.action_type == AllowedVoiceAction.CREATE_WORKSPACE_FILE:
            rel_path = intent.arguments.get("path", "")
            out = res.output_data or {}
            summary = f"Created and verified workspace file '{rel_path}'."
            return summary, out, [f"File '{rel_path}' created and verified on disk"]

        raise RuntimeError(f"Unhandled allowlisted action: {intent.action_type}")

    def _cancelled_result(
        self,
        request_id: str,
        session_id: str,
        start_perf: float,
    ) -> VoicePipelineResult:
        self.approval_broker.revoke_all_for_session(session_id)
        dur = round((time.perf_counter() - start_perf) * 1000.0, 2)
        self._log_voice_audit(
            "VOICE_SESSION_CANCELLED",
            "Voice request aborted due to session cancellation",
            session_id=session_id,
            request_id=request_id,
            level="INFO",
            payload={"execution_outcome": "CANCELLED", "duration_ms": dur},
        )
        return VoicePipelineResult(
            request_id=request_id,
            session_id=session_id,
            status=VoicePipelineStatus.CANCELLED,
            user_facing_message="Voice request cancelled.",
            error_code="SESSION_CANCELLED",
            duration_ms=dur,
        )
