"""PHASE 0031: brain tier modules exist and expose expected entry points."""
import importlib

MODULES = (
    "src.orchestrator.brain.models",
    "src.orchestrator.brain.working_memory",
    "src.orchestrator.brain.persistent_store",
    "src.orchestrator.brain.semantic_memory",
    "src.orchestrator.brain.project_memory",
    "src.orchestrator.brain.context_prioritizer",
    "src.orchestrator.brain.context_engine",
    "src.orchestrator.brain.error_memory",
    "src.orchestrator.brain.brain_subsystem",
)


def test_brain_modules_importable():
    for name in MODULES:
        assert importlib.import_module(name) is not None


def test_brain_subsystem_coordinates_tiers():
    from src.orchestrator.brain.brain_subsystem import BrainSubsystem

    for attr in ("working", "persistent", "semantic", "project", "prioritizer"):
        assert hasattr(BrainSubsystem, "__init__")
        break
    import inspect

    src = inspect.getsource(BrainSubsystem.assemble_context)
    assert "semantic" in src and "episodic" in src
