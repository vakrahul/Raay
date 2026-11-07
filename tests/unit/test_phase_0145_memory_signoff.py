"""PHASE 0145: memory area sign-off with stats, export, and correction."""
import json
from pathlib import Path

from src.orchestrator.brain.brain_subsystem import BrainSubsystem


def test_memory_area_signoff(tmp_path):
    brain = BrainSubsystem(workspace_root=Path.cwd(), db_path=tmp_path / "s45.db")
    brain.semantic.store_fact("fact45", "User prefers dark readable dashboards")
    assert brain.memory_stats()["total"] == 1
    exported = json.loads(brain.export_json())
    assert any(e["id"] == "fact45" for e in exported)
    assert brain.correct_memory("fact45", "User prefers dark dashboards") is True
    assert brain.delete_memory("fact45") is True
    assert brain.memory_stats()["total"] == 0
