"""PHASE 0187: retry spec declares status and bound contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_retry_spec_declares_contracts():
    text = (ROOT / "docs" / "EMPTY_ERROR_RETRY_SPEC.md").read_text(encoding="utf-8")
    for token in ("is_retryable_status", "429", "max_retries=3", "Empty prompts"):
        assert token in text
