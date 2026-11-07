"""PHASE 0121: task lifecycle primitives exist on both layers."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_task_lifecycle_primitives_present():
    from src.orchestrator.planner.task_models import TaskState

    assert {s.value for s in TaskState} >= {
        "PENDING", "IN_PROGRESS", "COMPLETED", "FAILED", "CANCELLED",
    }
    vm = (ROOT / "src" / "client" / "WinAI.Client" / "ViewModels" / "MainViewModel.cs").read_text(encoding="utf-8")
    assert "IsEmergencyStopped" in vm and "TriggerEmergencyStopAsync" in vm
