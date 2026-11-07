"""PHASE 0141: memory and provider controls exist on the Python side."""
import inspect

from src.orchestrator.brain import brain_subsystem as brain_mod
from src.orchestrator.brain.brain_subsystem import BrainSubsystem
from src.providers.registry import ProviderRegistry


def test_memory_controls_present():
    for method in ("inspect_memory", "correct_memory", "export_json", "delete_memory", "clear_all"):
        assert hasattr(BrainSubsystem, method)
    assert hasattr(ProviderRegistry, "list_providers")
    assert "memory" in inspect.getsource(brain_mod).lower()
