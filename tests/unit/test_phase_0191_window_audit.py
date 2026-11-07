"""PHASE 0191: compaction preserves security head and task state."""
from pathlib import Path

from src.orchestrator.brain.context_engine import AdvancedContextEngine


def test_compaction_preserves_invariants(tmp_path):
    engine = AdvancedContextEngine(workspace_root=Path.cwd())
    engine.initialize_task("t191", "Ship feature")
    compact = engine.build_compacted_context(max_tokens=512)
    assert "SECURITY" in compact
    assert "Ship feature" in compact


def test_prioritizer_module_present():
    import importlib

    assert importlib.import_module("src.orchestrator.brain.context_prioritizer") is not None
