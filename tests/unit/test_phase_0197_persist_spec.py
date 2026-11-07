"""PHASE 0197: persistence spec declares durability contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_persist_spec_declares_contracts():
    text = (ROOT / "docs" / "PERSISTENCE_SPEC.md").read_text(encoding="utf-8")
    for token in ("WAL", "count_by_type", "byte-identical"):
        assert token in text
