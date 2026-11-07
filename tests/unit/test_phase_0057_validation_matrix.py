"""PHASE 0057: validation matrix declares every bounded field."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_validation_matrix_covers_fields():
    text = (ROOT / "docs" / "CONFIG_VALIDATION_MATRIX.md").read_text(encoding="utf-8")
    for token in ("host", "1024", "anonical", "subprocess_timeout_seconds", "context_window_tokens"):
        assert token in text
