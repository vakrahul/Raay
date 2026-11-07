"""PHASE 0050: test-health area sign-off via CLI and suite counts."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_inventory_cli_exits_zero():
    proc = subprocess.run(
        [sys.executable, "scripts/check_test_inventory.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_suite_file_counts_healthy():
    unit = list((ROOT / "tests" / "unit").glob("test_*.py"))
    assert len(unit) >= 30
    assert len(list((ROOT / "tests" / "integration").glob("test_*.py"))) >= 2
    assert len(list((ROOT / "tests" / "security").glob("test_*.py"))) >= 2
