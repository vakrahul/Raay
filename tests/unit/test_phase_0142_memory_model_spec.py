"""PHASE 0142: memory/model spec declares control contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_memory_model_spec_declares_contracts():
    text = (ROOT / "docs" / "MEMORY_MODEL_CONFIG_SPEC.md").read_text(encoding="utf-8")
    for token in ("memory_stats", "export", "delete", "egress"):
        assert token in text
