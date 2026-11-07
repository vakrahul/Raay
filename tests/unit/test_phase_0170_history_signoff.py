"""PHASE 0170: history area sign-off with goal/observe/summarize flow."""
from src.orchestrator.brain.working_memory import WorkingMemory


def test_history_area_signoff():
    wm = WorkingMemory(session_id="sess_sign")
    wm.set_goal("Ship feature X", subtasks=["code", "test"])
    wm.set_variable("branch", "task/x")
    wm.record_observation("Tests green", tool_name="pytest")
    summary = wm.get_context_summary()
    assert "Ship feature X" in summary and "Tests green" in summary
    assert wm.get_variable("branch") == "task/x"
