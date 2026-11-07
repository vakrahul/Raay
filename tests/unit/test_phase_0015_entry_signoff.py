"""PHASE 0015: entry-point area sign-off across the core route surface."""
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app


def test_core_route_surface_signoff():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        for path in ("/health", "/api/v1/config", "/dashboard", "/"):
            res = client.get(path)
            assert res.status_code == 200, f"{path} returned {res.status_code}"
