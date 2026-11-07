"""PHASE 0067: logging spec declares record, redaction, integrity rules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_logging_spec_declares_contracts():
    text = (ROOT / "docs" / "LOGGING_SPEC.md").read_text(encoding="utf-8")
    for token in ("prev_hash", "redact_secrets", "verify_integrity", "SHA-256"):
        assert token in text
