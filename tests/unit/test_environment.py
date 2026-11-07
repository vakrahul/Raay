"""Verify reproducible development environment baseline."""
import sys
from pathlib import Path

def test_python_version_supported():
    """Ensure Python version satisfies requirement >= 3.11."""
    assert sys.version_info >= (3, 11)

def test_workspace_fixture(temp_workspace: Path):
    """Ensure workspace isolation fixture behaves as expected."""
    assert temp_workspace.exists()
    assert temp_workspace.is_dir()
    test_file = temp_workspace / "canary.txt"
    test_file.write_text("ok", encoding="utf-8")
    assert test_file.read_text(encoding="utf-8") == "ok"
