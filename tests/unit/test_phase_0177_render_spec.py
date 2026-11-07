"""PHASE 0177: render spec declares redaction contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_render_spec_declares_contracts():
    text = (ROOT / "docs" / "TOOL_RENDER_SPEC.md").read_text(encoding="utf-8")
    for token in ("render_tool_summary", "[REDACTED]", "200 characters"):
        assert token in text
