"""PHASE 0049: inventory checker detects missing suites and fixtures."""
from scripts.check_test_inventory import check_test_inventory


def test_missing_conftest_detected(tmp_path):
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_x.py").write_text("x")
    violations = check_test_inventory(tmp_path)
    assert any("conftest" in v for v in violations)


def test_empty_suite_detected(tmp_path):
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "tests" / "conftest.py").write_text("x")
    for suite in ("unit", "integration", "security"):
        (tmp_path / "tests" / suite).mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_x.py").write_text("x")
    violations = check_test_inventory(tmp_path)
    assert any("tests/integration" in v for v in violations)
    assert any("tests/security" in v for v in violations)
