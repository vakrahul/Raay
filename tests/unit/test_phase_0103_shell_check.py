"""PHASE 0103: client shell checker passes on the working tree."""
from scripts.check_client_shell import check_client_shell


def test_shell_check_reports_no_violations():
    assert check_client_shell() == []
