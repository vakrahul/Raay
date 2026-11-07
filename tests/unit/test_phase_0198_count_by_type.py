"""PHASE 0198: per-tier counts without content loading."""
from src.orchestrator.brain.models import MemoryEntry, MemoryType
from src.orchestrator.brain.persistent_store import PersistentMemoryStore


def test_count_by_type(tmp_path):
    store = PersistentMemoryStore(db_path=tmp_path / "c98.db")
    assert store.count_by_type(MemoryType.EPISODIC) == 0
    store.save_entry(MemoryEntry(id="e1", memory_type=MemoryType.EPISODIC, content="done A"))
    store.save_entry(MemoryEntry(id="e2", memory_type=MemoryType.EPISODIC, content="done B"))
    store.save_entry(MemoryEntry(id="s1", memory_type=MemoryType.SEMANTIC, content="fact"))
    assert store.count_by_type(MemoryType.EPISODIC) == 2
    assert store.count_by_type(MemoryType.SEMANTIC) == 1
