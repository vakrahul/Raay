"""PHASE 0097: diagnostics spec declares the report contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_diagnostics_spec_declares_contract():
    text = (ROOT / "docs" / "DIAGNOSTICS_SPEC.md").read_text(encoding="utf-8")
    for token in ("diagnostics.py", "route inventory", "never contain secrets", "Exit code 0"):
        assert token in text
