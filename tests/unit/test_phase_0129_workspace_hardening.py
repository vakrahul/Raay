"""PHASE 0129: workspace descriptor rejects relative roots and unknown tools."""
import pytest
from pydantic import ValidationError

from src.orchestrator.workspace_model import WorkspaceDescriptor


def test_relative_root_rejected():
    with pytest.raises(ValidationError):
        WorkspaceDescriptor(root_path="relative/path")


def test_unknown_tool_rejected():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValidationError) as excinfo:
            WorkspaceDescriptor(root_path=d, allowed_tools=["rm -rf"])
        assert "Unknown tools" in str(excinfo.value)


def test_bad_security_level_rejected():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValidationError):
            WorkspaceDescriptor(root_path=d, security_level="godmode")
