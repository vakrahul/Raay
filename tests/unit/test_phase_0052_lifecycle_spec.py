"""PHASE 0052: lifecycle spec declares startup ordering rules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_lifecycle_spec_declares_order():
    text = (ROOT / "docs" / "LIFECYCLE_SPEC.md").read_text(encoding="utf-8")
    for token in ("SecurityPolicyEngine", "ProviderRegistry", "list_registered_routes"):
        assert token in text
