"""PHASE 0132: integration spec declares connection contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_integration_spec_declares_contracts():
    text = (ROOT / "docs" / "INTEGRATION_CENTER_SPEC.md").read_text(encoding="utf-8")
    for token in ("IsLocal", "HasCredentialStored", "describe_connection", "egress"):
        assert token in text
