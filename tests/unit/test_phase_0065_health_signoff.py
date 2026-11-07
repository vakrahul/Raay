"""PHASE 0065: health area sign-off via live endpoint shape."""
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app


def test_health_area_signoff():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        data = client.get("/health").json()
        assert set(data) >= {
            "status",
            "app",
            "version",
            "environment",
            "provider",
            "provider_healthy",
            "configured_providers",
        }
