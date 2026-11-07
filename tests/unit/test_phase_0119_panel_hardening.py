"""PHASE 0119: panel status gate is case-sensitive and rejects blanks."""
from src.orchestrator.verification_reporter import is_known_panel_status


def test_panel_status_case_sensitive():
    assert is_known_panel_status("idle") is False
    assert is_known_panel_status("") is False
    assert is_known_panel_status("Completed ") is False
    assert is_known_panel_status("Completed") is True
