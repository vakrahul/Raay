"""PHASE 0182: approval UX spec declares display contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_approval_ux_spec_declares_contracts():
    text = (ROOT / "docs" / "APPROVAL_UX_SPEC.md").read_text(encoding="utf-8")
    for token in ("describe_request", "read-only", "nonce", "denial"):
        assert token in text.lower()
