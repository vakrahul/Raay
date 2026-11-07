"""PHASE 0117: activity spec declares status vocabulary and mapping."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_activity_spec_declares_contracts():
    text = (ROOT / "docs" / "AGENT_ACTIVITY_SPEC.md").read_text(encoding="utf-8")
    for token in ("AwaitingApproval", "Terminated", "ExecutionStage", "immutable"):
        assert token in text
