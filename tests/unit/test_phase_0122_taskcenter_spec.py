"""PHASE 0122: task-center spec declares progress and bucket contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_taskcenter_spec_declares_contracts():
    text = (ROOT / "docs" / "TASK_CENTER_SPEC.md").read_text(encoding="utf-8")
    for token in ("progress()", "AWAITING_APPROVAL", "IsEmergencyStopped", "100%"):
        assert token in text
