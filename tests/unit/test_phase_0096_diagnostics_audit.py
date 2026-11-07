"""PHASE 0096: diagnostics building blocks exist and are import-safe."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_diagnostics_building_blocks_present():
    for name in (
        "scripts/check_repo_layout.py",
        "scripts/check_packaging.py",
        "scripts/check_dependency_drift.py",
        "scripts/check_test_inventory.py",
    ):
        assert (ROOT / name).is_file()
    from src.orchestrator.main import describe_startup_order, list_registered_routes

    assert len(describe_startup_order()) == 8
    assert callable(list_registered_routes)
