"""PHASE 0166: working memory session primitives exist."""
from src.orchestrator.brain.working_memory import WorkingMemory


def test_history_primitives_present():
    wm = WorkingMemory(session_id="s")
    for method in ("set_goal", "set_variable", "get_variable", "record_observation", "get_context_summary", "clear"):
        assert hasattr(wm, method)
    assert wm.observations == [] and wm.current_goal is None
