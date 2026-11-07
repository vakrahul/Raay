"""PHASE 0120: activity area sign-off — C# enum mirrors Python vocabulary."""
from pathlib import Path

from src.orchestrator.verification_reporter import AGENT_PANEL_STATUSES

ROOT = Path(__file__).resolve().parents[2]


def test_cs_enum_mirrors_python_vocabulary():
    text = (
        ROOT / "src" / "client" / "WinAI.Client" / "Models" / "AgentActivityModel.cs"
    ).read_text(encoding="utf-8")
    for status in AGENT_PANEL_STATUSES:
        assert status in text
