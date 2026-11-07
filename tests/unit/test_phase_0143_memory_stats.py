"""PHASE 0143: memory statistics aggregate tier counts without content."""
from pathlib import Path

from src.orchestrator.brain.brain_subsystem import BrainSubsystem
from src.orchestrator.brain.models import MemoryType


def test_memory_stats_counts_tiers(tmp_path):
    brain = BrainSubsystem(workspace_root=Path.cwd(), db_path=tmp_path / "s43.db")
    stats = brain.memory_stats()
    assert stats["total"] == 0
    brain.semantic.store_fact("f43", "WinAI dashboard uses loopback IPC")
    stats = brain.memory_stats()
    assert stats[MemoryType.SEMANTIC.value] == 1
    assert stats["total"] == 1
