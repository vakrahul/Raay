"""PHASE 0062: health spec declares payload and failure rules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_health_spec_declares_contract():
    text = (ROOT / "docs" / "HEALTH_ENDPOINT_SPEC.md").read_text(encoding="utf-8")
    for token in ("degraded", "provider_healthy", "configured_providers", "500"):
        assert token in text
