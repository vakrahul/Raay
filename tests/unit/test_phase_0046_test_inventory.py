"""PHASE 0046: test pyramid structure exists with all three suites."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_test_suites_present():
    tests = ROOT / "tests"
    assert (tests / "conftest.py").is_file()
    for suite in ("unit", "integration", "security"):
        assert (tests / suite).is_dir()
        assert list((tests / suite).glob("test_*.py")), f"empty suite: {suite}"


def test_docs_baseline_present():
    docs = ROOT / "docs"
    assert (docs / "MASTER_ROADMAP_1000_PHASES.json").is_file()
    assert (docs / "IMPLEMENTATION_STATUS.md").is_file()
