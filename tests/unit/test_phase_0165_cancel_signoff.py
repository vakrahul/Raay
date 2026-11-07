"""PHASE 0165: cancellation area sign-off with unknown-action safety."""
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import build_cancelled_event, create_app


def test_cancel_area_signoff():
    assert build_cancelled_event()["event"] == "cancelled"
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/stream") as ws:
            ws.send_json({"action": "cancel"})
            assert ws.receive_json()["event"] == "cancelled"
