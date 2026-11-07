"""PHASE 0128: workspace descriptor validates root, level, and tools."""
from pathlib import Path

from src.orchestrator.workspace_model import WorkspaceDescriptor


def test_workspace_descriptor_roundtrip(tmp_path):
    ws = WorkspaceDescriptor(
        name="Demo",
        root_path=tmp_path,
        security_level="strict",
        allowed_tools=["fs_read_file", "terminal_run"],
    )
    assert ws.root_path.is_absolute()
    assert ws.allows_tool("terminal_run") is True
    assert ws.allows_tool("deploy_project") is False
    assert Path(str(ws.root_path)).exists()
