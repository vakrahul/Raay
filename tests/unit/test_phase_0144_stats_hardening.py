"""PHASE 0144: stats stay consistent across mutation edge cases."""
from pathlib import Path

from src.orchestrator.brain.brain_subsystem import BrainSubsystem


def test_stats_empty_and_unknown_delete(tmp_path):
    brain = BrainSubsystem(workspace_root=Path.cwd(), db_path=tmp_path / "s44.db")
    assert brain.memory_stats()["total"] == 0
    assert brain.delete_memory("ghost") is False
    assert brain.memory_stats()["total"] == 0
    brain.semantic.store_fact("a", "alpha content here")
    brain.semantic.store_fact("b", "beta content here")
    assert brain.memory_stats()["total"] == 2
    assert brain.delete_memory("a") is True
    assert brain.memory_stats()["total"] == 1
