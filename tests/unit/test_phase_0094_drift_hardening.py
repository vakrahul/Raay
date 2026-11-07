"""PHASE 0094: drift checker detects family mismatches in either direction."""
from scripts.check_dependency_drift import check_dependency_drift


def test_drift_detected_both_directions(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=["fastapi>=0.115.0", "httpx>=0.27.0"]\n'
    )
    (tmp_path / "requirements.txt").write_text("fastapi>=0.115.0\nrequests>=2.0\n")
    violations = check_dependency_drift(tmp_path)
    assert any("httpx" in v for v in violations)
    assert any("requests" in v for v in violations)
