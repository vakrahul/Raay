"""PHASE 0037: planner spec declares DAG and factory contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_planner_spec_declares_contracts():
    text = (ROOT / "docs" / "PLANNER_SPEC.md").read_text(encoding="utf-8")
    for token in ("validate_plan_dag", "allowed_tools", "depth-capped", "least-privilege"):
        assert token in text
