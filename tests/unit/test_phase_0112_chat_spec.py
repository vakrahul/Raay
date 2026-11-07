"""PHASE 0112: chat spec declares streaming and role contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_chat_spec_declares_contracts():
    text = (ROOT / "docs" / "CHAT_WORKSPACE_SPEC.md").read_text(encoding="utf-8")
    for token in ("IsStreaming", "IsGenerating", "CancellationTokenSource", "system|user|assistant|tool"):
        assert token in text
