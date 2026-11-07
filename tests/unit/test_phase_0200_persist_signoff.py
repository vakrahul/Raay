"""PHASE 0200: persistence sign-off with restart round-trip."""
from src.orchestrator.brain.models import MemoryEntry, MemoryType
from src.orchestrator.brain.persistent_store import PersistentMemoryStore


def test_restart_roundtrip_byte_identical(tmp_path):
    db = tmp_path / "c100.db"
    s1 = PersistentMemoryStore(db_path=db)
    s1.save_entry(MemoryEntry(id="r1", memory_type=MemoryType.EPISODIC, content="shipped v1"))
    s2 = PersistentMemoryStore(db_path=db)
    loaded = s2.get_entry("r1")
    assert loaded is not None and loaded.content == "shipped v1"
    assert s2.count_by_type(MemoryType.EPISODIC) == 1
