"""PHASE 0098: diagnostics collect route inventory with zero violations."""
from scripts.diagnostics import collect_diagnostics


def test_diagnostics_collect_clean():
    data = collect_diagnostics()
    assert "/health" in data["routes"]
    assert "/ws/v1/stream" in data["routes"]
    assert len(data["routes"]) >= 10
    assert all(v == [] for v in data["violations"].values())
