"""PHASE 0118: panel status vocabulary with terminal-state set."""
from src.orchestrator.verification_reporter import (
    AGENT_PANEL_STATUSES,
    TERMINAL_PANEL_STATUSES,
    is_known_panel_status,
)


def test_panel_vocabulary_complete():
    assert set(AGENT_PANEL_STATUSES) == {
        "Idle", "Planning", "ExecutingTool", "AwaitingApproval",
        "Completed", "Failed", "Terminated",
    }
    assert set(TERMINAL_PANEL_STATUSES) == {"Completed", "Failed", "Terminated"}
    assert is_known_panel_status("Planning") is True
    assert is_known_panel_status("Hacked") is False
