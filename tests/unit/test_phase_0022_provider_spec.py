"""PHASE 0022: provider contract spec exists and registry honors it."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_provider_spec_declares_contract():
    text = (ROOT / "docs" / "PROVIDER_CONTRACT_SPEC.md").read_text(encoding="utf-8")
    for token in ("complete", "get_capabilities", "health_check", "mock", "KeyError"):
        assert token in text
