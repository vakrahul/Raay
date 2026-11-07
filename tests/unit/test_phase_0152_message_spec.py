"""PHASE 0152: message spec declares shape contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_message_spec_declares_contracts():
    text = (ROOT / "docs" / "MESSAGE_CONTRACT_SPEC.md").read_text(encoding="utf-8")
    for token in ("tool_call_id", "has_tool_calls", "system|user|assistant|tool"):
        assert token in text
