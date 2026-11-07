"""PHASE 0107: navigation spec declares the destination contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_nav_spec_declares_contracts():
    text = (ROOT / "docs" / "NAVIGATION_SPEC.md").read_text(encoding="utf-8")
    for token in ("Chat", "Models", "Agents", "Security", "Memory", "NAV_DESTINATIONS"):
        assert token in text
