"""PHASE 0163: cancelled-event builder produces the protocol frame."""
from src.orchestrator.main import build_cancelled_event, is_known_ws_event


def test_cancelled_event_shape():
    frame = build_cancelled_event()
    assert frame == {"event": "cancelled"}
    assert is_known_ws_event(frame["event"]) is True
