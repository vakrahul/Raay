"""PHASE 0044: filesystem hardening — traversal blocked, unknown rollback safe."""
import pytest

from src.windows_integration.file_service import ScopedFileService


def test_absolute_and_parent_escapes_blocked(tmp_path):
    svc = ScopedFileService(tmp_path)
    with pytest.raises(PermissionError):
        svc.read_file("../../Windows/System32/drivers/etc/hosts")
    with pytest.raises(PermissionError):
        svc.write_file("/etc/passwd", "x")


def test_unknown_backup_rollback_returns_false(tmp_path):
    svc = ScopedFileService(tmp_path)
    assert svc.rollback("no_such_backup") is False
