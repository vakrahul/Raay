"""PHASE 0077: supervision spec declares timeout and cancellation rules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_supervision_spec_declares_contracts():
    text = (ROOT / "docs" / "ASYNC_SUPERVISION_SPEC.md").read_text(encoding="utf-8")
    for token in ("asyncio.wait_for", "max_duration_seconds", "cancel_plan", "CANCELLED"):
        assert token in text
