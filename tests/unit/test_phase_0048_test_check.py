"""PHASE 0048: test inventory check passes on the working tree."""
from scripts.check_test_inventory import check_test_inventory


def test_test_inventory_reports_no_violations():
    assert check_test_inventory() == []
