"""PHASE 0042: windows/security spec declares confinement contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_windows_security_spec_declares_contracts():
    text = (ROOT / "docs" / "WINDOWS_SECURITY_SPEC.md").read_text(encoding="utf-8")
    for token in ("realpath", "shell=False", "REQUIRE_APPROVAL", "CSPRNG", "fail closed"):
        assert token in text
