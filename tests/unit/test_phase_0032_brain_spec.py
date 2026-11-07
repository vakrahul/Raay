"""PHASE 0032: brain memory spec declares tier and provenance contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_brain_spec_declares_contracts():
    text = (ROOT / "docs" / "BRAIN_MEMORY_SPEC.md").read_text(encoding="utf-8")
    for token in ("Working", "SQLite", "cosine", "VERIFIED_FACT", "MODEL_HYPOTHESIS"):
        assert token in text
