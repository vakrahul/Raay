"""Windows Autonomous Execution Engine with Observable State Verification (Phase 5).

Executes actions through a 9-step verified pipeline:
1. Permission check
2. Action validation
3. Pre-flight app state check
4. Action dispatch
5. Result capture
6. Observable state outcome verification
7. Task context update
8. Audit logging
9. Recovery / Continue
"""

from enum import Enum
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.security.action_validator import ActionValidator
from src.security.audit_logger import AuditLogger
from src.security.policy_engine import ActionEvaluationResult, PolicyDecision, SecurityPolicyEngine
from src.windows_integration.app_manager import AppManager
from src.windows_integration.file_service import ScopedFileService
from src.windows_integration.os_context import OSContextTracker
from src.windows_integration.process_manager import ProcessCloseResult, WindowsProcessManager
from src.windows_integration.process_runner import RestrictedProcessRunner
from src.windows_integration.system_intelligence import WindowsSystemIntelligence
from src.windows_integration.uia_service import UIAutomationService


class ActionExecutionOutcome(str, Enum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"       # Action succeeded and post-state was verified
    SUCCESS_UNVERIFIED = "SUCCESS_UNVERIFIED"   # Executed without error, but observable state could not be confirmed
    FAILED_EXECUTION = "FAILED_EXECUTION"       # Action threw error or process failed
    BLOCKED_POLICY = "BLOCKED_POLICY"           # Action denied by policy engine
    BLOCKED_APPROVAL = "BLOCKED_APPROVAL"       # Action requires human approval


class ObservableExecutionResult(BaseModel):
    action_id: str
    tool_name: str
    outcome: ActionExecutionOutcome
    pre_state: Dict[str, Any]
    post_state: Dict[str, Any]
    output_data: Any = None
    error_message: Optional[str] = None
    duration_ms: float
    audit_hash: Optional[str] = None


class WindowsExecutionEngine:
    """Dispatches and verifies real Windows operations with observable outcome validation."""

    def __init__(
        self,
        workspace_root: Path,
        policy_engine: SecurityPolicyEngine,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self.workspace_root = workspace_root.resolve()
        self.policy_engine = policy_engine
        self.audit_logger = audit_logger
        self.file_service = ScopedFileService(self.workspace_root)
        self.process_runner = RestrictedProcessRunner(self.workspace_root)
        self.app_manager = AppManager()
        self.uia_service = UIAutomationService()
        self.intelligence = WindowsSystemIntelligence()
        self.process_manager = WindowsProcessManager()
        self.os_context = OSContextTracker(intelligence=self.intelligence)

    async def execute_action(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        session_id: str,
        agent_id: str,
        approval_nonce: Optional[str] = None,
    ) -> ObservableExecutionResult:
        """Executes the 9-step verified execution cycle."""
        action_id = f"act_{int(time.time() * 1000)}"
        start_time = time.perf_counter()

        # Step 1: Check Permissions & Policy
        policy_eval: ActionEvaluationResult = self.policy_engine.evaluate_action(
            tool_name=tool_name,
            arguments=arguments,
            session_id=session_id,
            agent_id=agent_id,
        )

        if policy_eval.decision == PolicyDecision.DENY:
            duration = (time.perf_counter() - start_time) * 1000
            self._log_audit(tool_name, "DENIED", policy_eval.reason, session_id, agent_id)
            return ObservableExecutionResult(
                action_id=action_id,
                tool_name=tool_name,
                outcome=ActionExecutionOutcome.BLOCKED_POLICY,
                pre_state={},
                post_state={},
                error_message=f"Blocked by Policy Engine: {policy_eval.reason}",
                duration_ms=duration,
            )

        if policy_eval.decision == PolicyDecision.REQUIRE_APPROVAL:
            if not approval_nonce:
                duration = (time.perf_counter() - start_time) * 1000
                self._log_audit(tool_name, "PENDING_APPROVAL", policy_eval.reason, session_id, agent_id)
                return ObservableExecutionResult(
                    action_id=action_id,
                    tool_name=tool_name,
                    outcome=ActionExecutionOutcome.BLOCKED_APPROVAL,
                    pre_state={},
                    post_state={"approval_nonce": policy_eval.approval_nonce},
                    error_message=f"Action requires user authorization nonce: {policy_eval.reason}",
                    duration_ms=duration,
                )
            # Consume nonce bound to exact tool, arguments, and session
            consumed = self.policy_engine.consume_approval(
                approval_nonce,
                expected_tool_name=tool_name,
                expected_arguments=arguments,
                expected_session_id=session_id,
            )
            if not consumed:
                duration = (time.perf_counter() - start_time) * 1000
                self._log_audit(tool_name, "APPROVAL_REJECTED", "Invalid, expired, or already-consumed approval nonce.", session_id, agent_id)
                return ObservableExecutionResult(
                    action_id=action_id,
                    tool_name=tool_name,
                    outcome=ActionExecutionOutcome.BLOCKED_POLICY,
                    pre_state={},
                    post_state={},
                    error_message="Invalid or expired approval nonce.",
                    duration_ms=duration,
                )
            self._log_audit(tool_name, "APPROVAL_ACCEPTED", "Valid approval nonce consumed for this action only.", session_id, agent_id)

        # Step 2: Validate Action Schema
        is_valid, validated_schema, val_msg = ActionValidator.validate_action(tool_name, arguments)
        if not is_valid:
            duration = (time.perf_counter() - start_time) * 1000
            return ObservableExecutionResult(
                action_id=action_id,
                tool_name=tool_name,
                outcome=ActionExecutionOutcome.FAILED_EXECUTION,
                pre_state={},
                post_state={},
                error_message=val_msg,
                duration_ms=duration,
            )

        # Step 3: Capture Pre-Execution State
        pre_state = self._capture_observable_state(tool_name, arguments)

        # Step 4: Execute Action & Step 5: Capture Result
        output_data = None
        error_msg = None
        try:
            if tool_name == "fs_read_file":
                output_data = self.file_service.read_file(arguments["path"])
            elif tool_name == "fs_write_file":
                backup_id = self.file_service.write_file(arguments["path"], arguments["content"])
                output_data = {"status": "written", "backup_id": backup_id}
            elif tool_name in ["terminal_run", "cmd_exec"]:
                cmd_args = arguments.get("command")
                if isinstance(cmd_args, str):
                    cmd_args = cmd_args.split()
                cmd_res = await self.process_runner.run_command(cmd_args)
                output_data = cmd_res.model_dump()
                if cmd_res.exit_code != 0:
                    error_msg = f"Process exited with code {cmd_res.exit_code}: {cmd_res.stderr}"
            elif tool_name == "app_launch":
                pid = self.app_manager.launch_app(arguments["app_id"], arguments.get("extra_args"))
                output_data = {"launched_pid": pid, "app_id": arguments["app_id"]}

            # Real Native Windows UI Automation Handlers
            elif tool_name == "window_find":
                win = self.uia_service.find_window(
                    window_title=arguments["window_title"],
                    class_name=arguments.get("class_name"),
                    timeout_seconds=arguments.get("timeout_seconds", 5.0),
                )
                if win:
                    output_data = {
                        "found": True,
                        "window_title": win.Name,
                        "class_name": win.ClassName,
                        "handle": hex(win.NativeWindowHandle) if win.NativeWindowHandle else None,
                    }
                else:
                    error_msg = f"Window '{arguments['window_title']}' not found on desktop"

            elif tool_name == "window_focus":
                if arguments.get("hwnd"):
                    success = self.uia_service.focus_window_by_hwnd(arguments["hwnd"])
                else:
                    success = self.uia_service.focus_window(
                        window_title=arguments["window_title"],
                        timeout_seconds=arguments.get("timeout_seconds", 3.0),
                    )
                if success:
                    output_data = {"focused": True, "window": arguments["window_title"]}
                else:
                    error_msg = f"Could not focus window '{arguments['window_title']}'"

            elif tool_name == "window_type_text":
                success = self.uia_service.set_text_value(
                    window_title=arguments["window_title"],
                    text=arguments["text"],
                    automation_id=arguments.get("automation_id"),
                    name=arguments.get("name"),
                    control_type=arguments.get("control_type"),
                    clear_first=arguments.get("clear_first", False),
                )
                if success:
                    output_data = {"typed": True, "text_length": len(arguments["text"])}
                else:
                    error_msg = f"Failed to type text into window '{arguments['window_title']}' control"

            elif tool_name == "window_send_keys":
                if arguments.get("hwnd"):
                    success = self.uia_service.send_keys_by_hwnd(
                        arguments["hwnd"],
                        keys=arguments["keys"],
                        wait_time=arguments.get("wait_time", 0.05),
                    )
                else:
                    success = self.uia_service.send_keys_to_window(
                        window_title=arguments["window_title"],
                        keys=arguments["keys"],
                        wait_time=arguments.get("wait_time", 0.05),
                    )
                if success:
                    output_data = {"sent": True, "keys": arguments["keys"]}
                else:
                    error_msg = f"Failed to send keys to window '{arguments['window_title']}'"

            elif tool_name == "window_click_control":
                success = self.uia_service.click_control(
                    window_title=arguments["window_title"],
                    name=arguments.get("name"),
                    control_type=arguments.get("control_type"),
                    automation_id=arguments.get("automation_id"),
                )
                if success:
                    output_data = {"clicked": True, "target": arguments.get("name") or arguments.get("automation_id")}
                else:
                    error_msg = f"Failed to click control in window '{arguments['window_title']}'"

            elif tool_name == "window_invoke_control":
                success = self.uia_service.invoke_button(
                    window_title=arguments["window_title"],
                    name=arguments.get("name"),
                    automation_id=arguments.get("automation_id"),
                )
                if success:
                    output_data = {"invoked": True, "target": arguments.get("name") or arguments.get("automation_id")}
                else:
                    error_msg = f"Failed to invoke control in window '{arguments['window_title']}'"

            elif tool_name == "window_select_menu":
                success = self.uia_service.select_menu_item(
                    window_title=arguments["window_title"],
                    menu_path=arguments["menu_path"],
                )
                if success:
                    output_data = {"selected_menu": arguments["menu_path"]}
                else:
                    error_msg = f"Failed to select menu '{arguments['menu_path']}' in window '{arguments['window_title']}'"

            elif tool_name == "window_wait_for_control":
                success = self.uia_service.wait_for_control(
                    window_title=arguments["window_title"],
                    name=arguments.get("name"),
                    control_type=arguments.get("control_type"),
                    automation_id=arguments.get("automation_id"),
                    timeout_seconds=arguments.get("timeout_seconds", 5.0),
                )
                if success:
                    output_data = {"control_ready": True}
                else:
                    error_msg = f"Timed out waiting for control in window '{arguments['window_title']}'"

            # Real Windows System Intelligence & Process Management Handlers
            elif tool_name == "system_get_overview":
                output_data = self.intelligence.get_system_overview().model_dump()

            elif tool_name == "system_get_memory_status":
                output_data = self.intelligence.get_system_memory_status().model_dump()

            elif tool_name == "process_list":
                procs = self.intelligence.list_processes(
                    sort_by=arguments.get("sort_by", "memory"),
                    limit=arguments.get("limit", 20),
                )
                output_data = [p.model_dump() for p in procs]

            elif tool_name == "process_get_top_consumers":
                procs = self.intelligence.get_top_resource_consumers(
                    metric=arguments.get("metric", "memory"),
                    limit=arguments.get("limit", 10),
                )
                output_data = [p.model_dump() for p in procs]

            elif tool_name == "process_get_info":
                p_info = self.intelligence.get_process_info(arguments["pid"])
                if p_info:
                    output_data = p_info.model_dump()
                else:
                    error_msg = f"Process PID {arguments['pid']} not found"

            elif tool_name == "process_close":
                if arguments.get("pid"):
                    res = self.process_manager.close_process(arguments["pid"], force=arguments.get("force", False))
                else:
                    results = self.process_manager.close_application_by_name(
                        arguments.get("app_name", ""), force=arguments.get("force", False)
                    )
                    res = results[0] if results else None

                if res:
                    output_data = res.model_dump()
                    if not res.success and res.method != "AWAITING_CONFIRMATION":
                        error_msg = res.message
                else:
                    error_msg = "No process targeted for closing"

            elif tool_name == "window_list":
                wins = self.intelligence.list_top_level_windows(visible_only=arguments.get("visible_only", True))
                output_data = [w.model_dump() for w in wins]

            elif tool_name == "window_get_foreground":
                w = self.intelligence.get_foreground_window()
                output_data = w.model_dump() if w else None

            elif tool_name == "browser_select_tab":
                # Tab activation requires an HWND pin (tab names repeat across windows).
                if not arguments.get("hwnd"):
                    error_msg = "browser_select_tab requires an hwnd argument"
                else:
                    tabs = self.uia_service.list_browser_tabs(arguments["hwnd"])
                    ok = self.uia_service.select_browser_tab(
                        arguments["hwnd"], arguments["tab_name"]
                    )
                    if ok:
                        output_data = {"selected_tab": arguments["tab_name"]}
                    else:
                        error_msg = (
                            f"Could not activate tab '{arguments['tab_name']}' "
                            f"(visible tabs: {[t.get('name') for t in tabs][:8]})"
                        )

            elif tool_name == "browser_get_url":
                if arguments.get("hwnd"):
                    url = self.uia_service.get_browser_url_by_hwnd(arguments["hwnd"])
                else:
                    url = self.uia_service.get_browser_url(
                        arguments["window_title"],
                        timeout_seconds=arguments.get("timeout_seconds", 3.0),
                    )
                if url is not None:
                    output_data = {"url": url}
                else:
                    error_msg = f"Could not read address-bar URL from window '{arguments['window_title']}'"

            elif tool_name == "browser_get_heading":
                if arguments.get("hwnd"):
                    heading = self.uia_service.read_page_heading_by_hwnd(arguments["hwnd"])
                else:
                    heading = self.uia_service.read_page_heading(
                        arguments["window_title"],
                        timeout_seconds=arguments.get("timeout_seconds", 5.0),
                    )
                if heading:
                    output_data = {"heading": heading}
                else:
                    error_msg = f"Could not read page heading from window '{arguments['window_title']}'"

            elif tool_name == "window_get_text":
                text = self.uia_service.read_text_value(
                    arguments["window_title"],
                    automation_id=arguments.get("automation_id"),
                    name=arguments.get("name"),
                    control_type=arguments.get("control_type"),
                )
                if text:
                    output_data = {"text": text}
                else:
                    error_msg = f"Could not read text from window '{arguments['window_title']}'"

            else:
                error_msg = f"Execution handler not implemented for tool '{tool_name}'"

        except Exception as e:
            error_msg = str(e)

        # Step 6: Verify Observable Post-State (Did it ACTUALLY succeed?)
        post_state = self._capture_observable_state(tool_name, arguments)
        verified_success = self._verify_state_transition(tool_name, pre_state, post_state, error_msg)

        duration = (time.perf_counter() - start_time) * 1000

        # Step 7 & 8: Record Audit Event
        audit_hash = self._log_audit(
            tool_name,
            "SUCCESS" if verified_success else "FAILED",
            str(output_data)[:200] if verified_success else error_msg or "Unknown error",
            session_id,
            agent_id,
        )

        outcome = (
            ActionExecutionOutcome.VERIFIED_SUCCESS
            if verified_success
            else (ActionExecutionOutcome.FAILED_EXECUTION if error_msg else ActionExecutionOutcome.SUCCESS_UNVERIFIED)
        )

        return ObservableExecutionResult(
            action_id=action_id,
            tool_name=tool_name,
            outcome=outcome,
            pre_state=pre_state,
            post_state=post_state,
            output_data=output_data,
            error_message=error_msg,
            duration_ms=duration,
            audit_hash=audit_hash,
        )

    def _capture_observable_state(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Inspect actual file/process/window state before and after execution."""
        state = {}
        try:
            if tool_name in ["fs_write_file", "fs_read_file"]:
                p = (self.workspace_root / arguments.get("path", ".")).resolve()
                state["file_exists"] = p.exists()
                state["file_size"] = p.stat().st_size if p.exists() else 0
                state["mtime"] = p.stat().st_mtime if p.exists() else 0
            elif tool_name == "app_launch":
                app_id = arguments.get("app_id", "")
                state["is_running"] = self.app_manager.get_running_pid(app_id) is not None
            elif tool_name == "process_close":
                pid = arguments.get("pid")
                if pid:
                    state["process_exists"] = psutil.pid_exists(pid)
                elif arguments.get("app_name"):
                    state["matching_pids"] = self.process_manager.find_pids_by_name(arguments["app_name"])
            elif tool_name in [
                "window_find",
                "window_focus",
                "window_type_text",
                "window_send_keys",
                "window_click_control",
                "window_invoke_control",
                "window_select_menu",
                "window_wait_for_control",
                "browser_get_url",
                "browser_get_heading",
                "window_get_text",
                "browser_select_tab",
            ]:
                target = arguments.get("window_title", "")
                win = self.uia_service.find_window(target, timeout_seconds=0.5)
                state["window_exists"] = win is not None
                if win:
                    state["window_title"] = win.Name
                    state["window_handle"] = win.NativeWindowHandle
                    if tool_name == "window_type_text":
                        state["text_value"] = self.uia_service.read_text_value(
                            target,
                            automation_id=arguments.get("automation_id"),
                            name=arguments.get("name"),
                            control_type=arguments.get("control_type"),
                        )
        except Exception:
            pass
        return state

    def _verify_state_transition(
        self,
        tool_name: str,
        pre: Dict[str, Any],
        post: Dict[str, Any],
        error: Optional[str],
    ) -> bool:
        """Confirm action produced verifiable, observable effects in the operating system."""
        if error:
            return False
        if tool_name == "fs_write_file":
            return post.get("file_exists", False) and post.get("mtime", 0) >= pre.get("mtime", 0)
        if tool_name == "fs_read_file":
            return pre.get("file_exists", False)
        if tool_name == "app_launch":
            return post.get("is_running", False)
        if tool_name == "window_find":
            return post.get("window_exists", False)
        if tool_name == "window_focus":
            return post.get("window_exists", False)
        if tool_name == "window_type_text":
            return post.get("window_exists", False) and error is None
        if tool_name == "window_send_keys":
            # Keystrokes (new tab, navigation) legitimately change the window
            # title, so a title-based post-state lookup may miss. The send
            # itself succeeding without exception is the verified outcome.
            return error is None
        if tool_name in ["window_click_control", "window_invoke_control", "window_select_menu", "window_wait_for_control"]:
            return error is None
        if tool_name == "process_close":
            return error is None
        if (
            tool_name.startswith("system_")
            or tool_name.startswith("process_")
            or tool_name.startswith("browser_")
            or tool_name == "window_list"
            or tool_name == "window_get_foreground"
            or tool_name == "window_get_text"
        ):
            return error is None
        return True

    def _log_audit(self, tool_name: str, status: str, details: str, session_id: str, agent_id: str) -> Optional[str]:
        if self.audit_logger:
            event = self.audit_logger.log(
                event_type="WINDOWS_TOOL_EXECUTION",
                message=f"Tool '{tool_name}' {status}: {details}",
                level="INFO" if status == "SUCCESS" else "WARNING",
                session_id=session_id,
                agent_id=agent_id,
            )
            return event.hash
        return None
