"""PHASE 0075: error area sign-off via live denial surfaces."""
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app


def test_error_area_signoff():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/approval/respond",
            json={"approval_nonce": "0" * 64, "user_decision": "APPROVED"},
        )
        assert res.status_code == 404
        assert "Invalid or expired" in res.json()["detail"]
