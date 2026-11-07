"""PHASE 0043: safe path joining without filesystem side effects."""
import pytest

from src.windows_integration.file_service import ScopedFileService


def test_safe_join_confines_paths(tmp_path):
    svc = ScopedFileService(tmp_path)
    assert svc.safe_join("a", "b.txt") == (tmp_path / "a" / "b.txt").resolve()
    with pytest.raises(PermissionError):
        svc.safe_join("..", "outside.txt")
