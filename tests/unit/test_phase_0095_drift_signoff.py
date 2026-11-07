"""PHASE 0095: drift area sign-off via CLI exit code."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_drift_cli_exits_zero():
    proc = subprocess.run(
        [sys.executable, "scripts/check_dependency_drift.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "in sync" in proc.stdout.lower()
