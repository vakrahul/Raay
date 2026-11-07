"""PHASE 0192: windowing spec declares head-preserving trim rules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_windowing_spec_declares_contracts():
    text = (ROOT / "docs" / "WINDOWING_SPEC.md").read_text(encoding="utf-8")
    for token in ("window_messages", "system", "max_messages", "never"):
        assert token in text
