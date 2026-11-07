"""PHASE 0148: accessibility checker passes on the working tree."""
from scripts.check_accessibility import check_accessibility


def test_accessibility_check_reports_no_violations():
    assert check_accessibility() == []
