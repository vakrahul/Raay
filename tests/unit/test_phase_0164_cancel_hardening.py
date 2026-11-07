"""PHASE 0164: repeated cancels are safe and idempotent."""
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app


def test_double_cancel_safe():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1/stream") as ws:
            for _ in range(2):
                ws.send_json({"action": "cancel"})
                assert ws.receive_json() == {"event": "cancelled"}
