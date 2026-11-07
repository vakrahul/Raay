"""PHASE 0034: brain hardening — empty state and unknown IDs fail safe."""
from pathlib import Path

from src.orchestrator.brain.brain_subsystem import BrainSubsystem


def test_empty_brain_assembles_without_error(tmp_path):
    brain = BrainSubsystem(workspace_root=Path.cwd(), db_path=tmp_path / "t4.db")
    context = brain.assemble_context("hello world", max_tokens=512)
    assert isinstance(context, str)


def test_unknown_memory_correction_returns_false(tmp_path):
    brain = BrainSubsystem(workspace_root=Path.cwd(), db_path=tmp_path / "t4b.db")
    assert brain.correct_memory("no_such_entry", "new content") is False
    assert brain.delete_memory("no_such_entry") is False
