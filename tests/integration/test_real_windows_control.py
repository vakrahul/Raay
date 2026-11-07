"""Genuine Native Windows Desktop Integration Tests (Real Windows UI Automation).

These tests run on the actual Windows desktop environment:
- Launch real applications (Notepad, Calculator).
- Inspect accessible UI Automation control trees.
- Set text directly via UIA / SendKeys.
- Execute multi-step Save As workflows.
- Verify actual filesystem state transitions.
- Enforce strict security policy rules and rejection of unapproved binaries.
Zero mocks. Zero remote vision loops. Zero simulation.
"""

import os
from pathlib import Path
import subprocess
import time
import pytest

from src.orchestrator.task_dispatcher import AutonomousTaskDispatcher
from src.security.policy_engine import ActionEvaluationResult, PolicyDecision, RiskTier, SecurityPolicyEngine
from src.windows_integration.execution_engine import ActionExecutionOutcome, WindowsExecutionEngine
from src.windows_integration.uia_service import UIAutomationService


@pytest.fixture(autouse=True)
def clean_desktop_processes():
    """Ensure no leftover Notepad or Calculator processes before or after tests."""
    yield
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Stop-Process -Name notepad, CalculatorApp -Force -ErrorAction SilentlyContinue"],
        capture_output=True,
    )


@pytest.mark.integration
def test_real_uia_window_inspection_and_focus():
    """Verify genuine UI Automation element discovery and focus management on real Notepad."""
    proc = subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    try:
        uia = UIAutomationService()
        win = uia.find_window("Notepad", timeout_seconds=4.0)
        assert win is not None, "Real Notepad window was not found on desktop"

        # Focus window
        focused = uia.focus_window("Notepad")
        assert focused is True, "Failed to focus real Notepad window"

        # Inspect real elements
        elements = uia.inspect_window_elements("Notepad", max_depth=3)
        assert len(elements) > 0, "Real Notepad element tree returned 0 controls"

        # Find document or edit control
        ctrl_types = [e.control_type for e in elements]
        assert any(t in ["DocumentControl", "EditControl", "PaneControl"] for t in ctrl_types)

    finally:
        proc.terminate()
        time.sleep(0.5)


@pytest.mark.integration
def test_real_uia_missing_control_explicit_failure():
    """Verify that looking for non-existent windows or controls returns explicit failure, not mock success."""
    uia = UIAutomationService()
    win = uia.find_window("GhostApp_DefinitelyDoesNotExist_9999", timeout_seconds=0.5)
    assert win is None

    ctrl = uia.locate_control("GhostApp_9999", name="GhostButton", timeout_seconds=0.5)
    assert ctrl is None

    val = uia.read_text_value("GhostApp_9999", name="GhostText")
    assert val is None

    success = uia.set_text_value("GhostApp_9999", "Some Text")
    assert success is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_execution_engine_security_policy_enforcement(temp_workspace: Path):
    """Verify that desktop actions pass through the security policy engine and block unauthorized actions."""
    policy = SecurityPolicyEngine(workspace_root=temp_workspace, require_approvals=False)
    engine = WindowsExecutionEngine(workspace_root=temp_workspace, policy_engine=policy)

    # 1. Unapproved binary launch -> MUST BE DENIED BY POLICY
    deny_res = await engine.execute_action(
        tool_name="app_launch",
        arguments={"app_id": "malicious_rootkit_executable"},
        session_id="sec_test",
        agent_id="agt_sec",
    )
    assert deny_res.outcome == ActionExecutionOutcome.BLOCKED_POLICY
    assert "not in approved application whitelist" in deny_res.error_message

    # 2. Null-byte injection attempt in window title -> MUST FAIL SCHEMA VALIDATION
    null_byte_res = await engine.execute_action(
        tool_name="window_focus",
        arguments={"window_title": "Notepad\x00Exploit"},
        session_id="sec_test",
        agent_id="agt_sec",
    )
    assert null_byte_res.outcome == ActionExecutionOutcome.FAILED_EXECUTION
    assert "Null byte detected" in null_byte_res.error_message


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_notepad_type_and_save_workflow(temp_workspace: Path):
    """Verify the complete user workflow: Open Notepad, type text, save to disk, and verify content."""
    dispatcher = AutonomousTaskDispatcher(workspace_root=temp_workspace)
    save_filename = "verified_hello_rahul.txt"
    target_path = temp_workspace / save_filename
    if target_path.exists():
        target_path.unlink()

    prompt = f"Open Notepad, type Hello Rahul, and save it as {save_filename}."
    result = await dispatcher.execute_task(prompt)

    # 1. Dispatcher report assertions
    assert result.status == "COMPLETED", f"Execution failed: {result.summary}"
    assert result.action_type == "desktop_control"

    # 2. Genuine filesystem outcome verification
    assert target_path.exists(), f"Target file '{save_filename}' was not created on disk"
    assert target_path.stat().st_size > 0

    try:
        content = target_path.read_text(encoding="utf-8")
    except Exception:
        content = target_path.read_text(encoding="cp1252", errors="replace")

    assert "Hello Rahul" in content, f"Expected text 'Hello Rahul' not found in file: '{content}'"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_calculator_automation(temp_workspace: Path):
    """Verify real Calculator application launching and keystroke automation."""
    dispatcher = AutonomousTaskDispatcher(workspace_root=temp_workspace)
    prompt = "Open Calculator and calculate 7+5"
    result = await dispatcher.execute_task(prompt)

    assert result.status == "COMPLETED"
    assert result.action_type == "desktop_control"
    assert any("window_send_keys" in ev for ev in result.observable_evidence)
