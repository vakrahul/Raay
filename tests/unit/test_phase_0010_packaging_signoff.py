"""PHASE 0010: packaging area sign-off via CLI exit code."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_packaging_cli_exits_zero():
    proc = subprocess.run(
        [sys.executable, "scripts/check_packaging.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "compliant" in proc.stdout.lower()
