"""PHASE 0072: error contract spec declares hierarchy and mapping."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_error_spec_declares_contracts():
    text = (ROOT / "docs" / "ERROR_CONTRACT_SPEC.md").read_text(encoding="utf-8")
    for token in ("WinAIError", "SecurityViolationError", "ToolValidationError", "404"):
        assert token in text
