"""PHASE 0130: workspace area sign-off with strict least-privilege default."""
from src.orchestrator.workspace_model import KNOWN_TOOLS, WorkspaceDescriptor


def test_workspace_area_signoff(tmp_path):
    ws = WorkspaceDescriptor(root_path=tmp_path)
    assert ws.security_level == "strict"
    assert ws.allowed_tools == []
    assert ws.allows_tool("fs_read_file") is False
    assert "fs_write_file" in KNOWN_TOOLS and "deploy_project" in KNOWN_TOOLS
