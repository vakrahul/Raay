"""PHASE 0085: websocket area sign-off via live cancel round-trip."""
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app


def test_ws_cancel_roundtrip():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/stream") as ws:
            ws.send_json({"action": "cancel"})
            msg = ws.receive_json()
            assert msg["event"] == "cancelled"
