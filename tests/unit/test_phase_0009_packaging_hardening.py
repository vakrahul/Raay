"""PHASE 0009: packaging checker edge cases."""
from scripts.check_packaging import check_packaging


def test_missing_manifest_detected(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\ndependencies=[]\n")
    violations = check_packaging(tmp_path)
    assert any("missing required manifest" in v for v in violations)


def test_live_tree_still_compliant():
    assert check_packaging() == []
