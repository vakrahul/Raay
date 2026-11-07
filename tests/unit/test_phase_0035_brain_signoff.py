"""PHASE 0035: brain area sign-off across store and retrieve."""
from pathlib import Path

from src.orchestrator.brain.brain_subsystem import BrainSubsystem


def test_brain_store_retrieve_roundtrip(tmp_path):
    brain = BrainSubsystem(workspace_root=Path.cwd(), db_path=tmp_path / "t5.db")
    brain.semantic.store_fact("fact_signoff", "WinAI uses loopback IPC on port 8765")
    brain.working.record_observation("Observed test run green")
    context = brain.assemble_context("loopback IPC port", max_tokens=1024)
    assert isinstance(context, str) and len(context) > 0
    assert brain.delete_memory("fact_signoff") is True
