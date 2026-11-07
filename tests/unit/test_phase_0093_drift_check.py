"""PHASE 0093: drift checker passes on the working tree."""
from scripts.check_dependency_drift import check_dependency_drift


def test_no_drift_in_working_tree():
    assert check_dependency_drift() == []
