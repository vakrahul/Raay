"""PHASE 0003: layout enforcement check passes on the working tree."""
from scripts.check_repo_layout import check_layout


def test_layout_check_reports_no_violations():
    assert check_layout() == []
