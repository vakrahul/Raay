"""PHASE 0169: history edge cases fail safe."""
from src.orchestrator.brain.working_memory import WorkingMemory


def test_history_edge_cases():
    wm = WorkingMemory(session_id="s")
    assert wm.recent_observations(limit=0) == []
    assert wm.recent_observations(limit=-3) == []
    assert wm.get_context_summary() == ""
    assert wm.get_variable("missing", default="dflt") == "dflt"
    wm.set_goal("g", subtasks=["a"])
    wm.clear()
    assert wm.current_goal is None and wm.observations == [] and wm.variables == {}
