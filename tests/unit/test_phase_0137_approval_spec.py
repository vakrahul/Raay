"""PHASE 0137: approval spec declares nonce and default-deny contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_approval_spec_declares_contracts():
    text = (ROOT / "docs" / "APPROVAL_CENTER_SPEC.md").read_text(encoding="utf-8")
    for token in ("single-use nonce", "action hash", "Deny is the default"):
        assert token in text
