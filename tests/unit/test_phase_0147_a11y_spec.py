"""PHASE 0147: accessibility spec declares the button-name contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_a11y_spec_declares_contracts():
    text = (ROOT / "docs" / "ACCESSIBILITY_SPEC.md").read_text(encoding="utf-8")
    for token in ("AutomationProperties.Name", "check_accessibility.py", "Emergency"):
        assert token in text
