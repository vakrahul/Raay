"""PHASE 0084: vocabulary gate rejects injection-styled event names."""
from src.orchestrator.main import WS_INBOUND_ACTIONS, is_known_ws_event


def test_hostile_event_names_rejected():
    for hostile in ("", "TOKEN", "done ", "tool_proposal;rm", "<script>", "chat"):
        assert is_known_ws_event(hostile) is False
    assert "chat" in WS_INBOUND_ACTIONS


def test_known_outbound_events_accepted():
    for event in ("token", "tool_proposal", "done", "cancelled"):
        assert is_known_ws_event(event) is True
