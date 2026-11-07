"""PHASE 0091: packaging manifests exist for drift auditing."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_manifests_present_for_drift_audit():
    for name in ("pyproject.toml", "requirements.txt", "requirements-dev.txt"):
        assert (ROOT / name).is_file()
