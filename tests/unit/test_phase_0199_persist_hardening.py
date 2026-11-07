"""PHASE 0199: unknown IDs and missing rows fail safe."""
from src.orchestrator.brain.persistent_store import PersistentMemoryStore


def test_unknown_entry_access_safe(tmp_path):
    store = PersistentMemoryStore(db_path=tmp_path / "c99.db")
    assert store.get_entry("ghost") is None
    assert store.delete_entry("ghost") is False
