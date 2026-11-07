"""PHASE 0168: parameterized recent-observation accessor."""
from src.orchestrator.brain.working_memory import WorkingMemory


def test_recent_observations_newest_first():
    wm = WorkingMemory(session_id="s")
    for i in range(4):
        wm.record_observation(f"obs {i}")
    recent = wm.recent_observations(limit=2)
    assert [e.content for e in recent] == ["obs 3", "obs 2"]
    assert len(wm.recent_observations(limit=10)) == 4
