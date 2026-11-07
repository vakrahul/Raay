"""PHASE 0033: tier inventory helper reports the active memory tiers."""
from pathlib import Path

from src.orchestrator.brain.brain_subsystem import BrainSubsystem


def test_tier_names_lists_active_tiers(tmp_path):
    brain = BrainSubsystem(workspace_root=Path.cwd(), db_path=tmp_path / "t3.db")
    assert brain.tier_names() == ["working", "persistent", "semantic", "project", "prioritizer"]
