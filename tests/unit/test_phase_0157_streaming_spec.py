"""PHASE 0157: streaming spec declares chunk and truncation contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_streaming_spec_declares_contracts():
    text = (ROOT / "docs" / "STREAMING_SPEC.md").read_text(encoding="utf-8")
    for token in ("collect_stream", "max_chars", "CancelledError"):
        assert token in text
