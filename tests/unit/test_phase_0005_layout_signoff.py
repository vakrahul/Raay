"""PHASE 0005: layout area sign-off via CLI exit code and regression gates."""
import subprocess
import sys


def test_layout_cli_exits_zero():
    proc = subprocess.run(
        [sys.executable, "scripts/check_repo_layout.py"],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "compliant" in proc.stdout.lower()
