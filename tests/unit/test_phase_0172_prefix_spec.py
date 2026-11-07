"""PHASE 0172: prefix spec declares cache contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_prefix_spec_declares_contracts():
    text = (ROOT / "docs" / "PREFIX_CACHE_SPEC.md").read_text(encoding="utf-8")
    for token in ("invalidate_model", "zero new tokens", "threshold"):
        assert token in text
