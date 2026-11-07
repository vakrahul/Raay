"""PHASE 0007: packaging spec exists and matches declared manifests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_packaging_spec_declares_manifest_roles():
    text = (ROOT / "docs" / "PACKAGING_SPEC.md").read_text(encoding="utf-8")
    for token in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "requires-python"):
        assert token in text
