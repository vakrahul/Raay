"""PHASE 0105: shell area sign-off via CLI exit code."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_shell_cli_exits_zero():
    proc = subprocess.run(
        [sys.executable, "scripts/check_client_shell.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "valid" in proc.stdout.lower()
