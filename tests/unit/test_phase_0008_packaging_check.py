"""PHASE 0008: packaging enforcement check passes on the working tree."""
from scripts.check_packaging import check_packaging


def test_packaging_check_reports_no_violations():
    assert check_packaging() == []
