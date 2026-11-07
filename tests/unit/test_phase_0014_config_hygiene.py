"""PHASE 0014: entry-point hardening — config surface exposes no secrets."""
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app


def test_config_endpoint_exposes_no_secrets():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        res = client.get("/api/v1/config")
        assert res.status_code == 200
        body = res.text.lower()
        for token in ("api_key", "apikey", "secret", "token", "password", "bearer"):
            assert token not in body, f"config surface leaks: {token}"


def test_health_endpoint_never_embeds_credentials():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200
        body = res.text.lower()
        assert "sk-" not in body and "bearer" not in body
