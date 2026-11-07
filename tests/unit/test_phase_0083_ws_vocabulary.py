"""PHASE 0083: websocket vocabulary helpers accept protocol events only."""
from src.orchestrator.main import WS_INBOUND_ACTIONS, WS_OUTBOUND_EVENTS, is_known_ws_event


def test_ws_vocabulary_helpers():
    assert set(WS_OUTBOUND_EVENTS) == {"token", "tool_proposal", "done", "cancelled"}
    assert set(WS_INBOUND_ACTIONS) == {"chat", "cancel"}
    assert is_known_ws_event("token") is True
    assert is_known_ws_event("rm -rf") is False
