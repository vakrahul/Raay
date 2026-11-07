"""PHASE 0127: workspace spec declares descriptor contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_workspace_spec_declares_contracts():
    text = (ROOT / "docs" / "PROJECT_WORKSPACE_SPEC.md").read_text(encoding="utf-8")
    for token in ("absolute", "strict", "allowed_tools", "never overwritten"):
        assert token in text
