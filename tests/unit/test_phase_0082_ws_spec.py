"""PHASE 0082: websocket spec declares the event vocabulary."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ws_spec_declares_vocabulary():
    text = (ROOT / "docs" / "WEBSOCKET_SPEC.md").read_text(encoding="utf-8")
    for token in ("token", "tool_proposal", "done", "cancelled", "chat"):
        assert token in text
