"""PHASE 0099: diagnostics output carries no secret material."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_diagnostics_output_secret_free():
    proc = subprocess.run(
        [sys.executable, "scripts/diagnostics.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = proc.stdout.lower()
    for token in ("sk-", "vach_", "api_key", "bearer", "password", "secret"):
        assert token not in blob
