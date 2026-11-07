"""PHASE 0081: websocket event protocol exists in the endpoint."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ws_protocol_markers_present():
    text = (ROOT / "src" / "orchestrator" / "main.py").read_text(encoding="utf-8")
    for token in ('"/ws/v1/stream"', '"token"', '"tool_proposal"', '"done"', '"cancelled"', "WebSocketDisconnect"):
        assert token in text
