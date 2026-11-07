"""Global test configuration and fixtures."""
import pytest
from pathlib import Path
import tempfile
import shutil

@pytest.fixture
def temp_workspace():
    """Create a temporary directory simulating a restricted user workspace."""
    temp_dir = tempfile.mkdtemp(prefix="winai_test_ws_")
    ws_path = Path(temp_dir).resolve()
    yield ws_path
    shutil.rmtree(temp_dir, ignore_errors=True)
