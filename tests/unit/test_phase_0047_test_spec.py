"""PHASE 0047: test health spec declares suite contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_health_spec_declares_contracts():
    text = (ROOT / "docs" / "TEST_HEALTH_SPEC.md").read_text(encoding="utf-8")
    for token in ("tests/unit/", "tests/integration/", "tests/security/", "fail closed"):
        assert token in text
