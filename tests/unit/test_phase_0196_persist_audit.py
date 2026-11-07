"""PHASE 0196: persistence primitives exist with WAL durability."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_persistence_primitives_present():
    text = (ROOT / "src" / "orchestrator" / "brain" / "persistent_store.py").read_text(encoding="utf-8")
    assert "journal_mode=WAL" in text
    for method in ("save_entry", "get_entry", "search_by_type", "delete_entry", "clear"):
        assert method in text
