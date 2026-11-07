"""PHASE 0064: health endpoint never leaks credentials, even when degraded."""
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app


def test_health_never_embeds_secrets():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200
        body = res.text.lower()
        for token in ("sk-", "api_key", "bearer", "password", "secret"):
            assert token not in body
        assert res.json()["status"] in ("healthy", "degraded")
