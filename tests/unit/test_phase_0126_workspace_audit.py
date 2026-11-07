"""PHASE 0126: workspace descriptor and Python project modules exist."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_workspace_model_contract():
    text = (
        ROOT / "src" / "client" / "WinAI.Client" / "Models" / "WorkspaceModel.cs"
    ).read_text(encoding="utf-8")
    for token in ("RootPath", "SecurityLevel", "AllowedTools", "CreatedAt"):
        assert token in text


def test_python_project_modules_present():
    import importlib

    for name in (
        "src.orchestrator.brain.project_memory",
        "src.orchestrator.workspace_ingestion",
        "src.orchestrator.project_builder",
    ):
        assert importlib.import_module(name) is not None
