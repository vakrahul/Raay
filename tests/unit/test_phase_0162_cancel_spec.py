"""PHASE 0162: cancellation spec declares idempotent cancel contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cancel_spec_declares_contracts():
    text = (ROOT / "docs" / "CANCELLATION_SPEC.md").read_text(encoding="utf-8")
    for token in ("build_cancelled_event", "cancelled", "CancelledError"):
        assert token in text
