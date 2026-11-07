"""PHASE 0167: history spec declares windowing contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_history_spec_declares_contracts():
    text = (ROOT / "docs" / "HISTORY_SPEC.md").read_text(encoding="utf-8")
    for token in ("recent_observations", "verification status", "clear()"):
        assert token in text
