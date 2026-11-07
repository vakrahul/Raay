"""PHASE 0092: reproducibility spec declares the mirror rule."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_repro_spec_declares_mirror_rule():
    text = (ROOT / "docs" / "DEPENDENCY_REPRO_SPEC.md").read_text(encoding="utf-8")
    for token in ("pyproject.toml", "requirements.txt", "identical", "lower-bound"):
        assert token in text
