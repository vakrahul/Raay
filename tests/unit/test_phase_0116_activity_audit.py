"""PHASE 0116: agent activity model exposes status lifecycle fields."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "src" / "client" / "WinAI.Client" / "Models" / "AgentActivityModel.cs"


def test_activity_model_contract():
    text = MODEL.read_text(encoding="utf-8")
    for token in ("AgentId", "RoleName", "CurrentTask", "AgentStatus", "LastToolExecuted", "LastUpdated"):
        assert token in text
    for status in ("Idle", "Planning", "ExecutingTool", "AwaitingApproval", "Completed", "Failed", "Terminated"):
        assert status in text
