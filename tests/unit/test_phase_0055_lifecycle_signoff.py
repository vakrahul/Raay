"""PHASE 0055: lifecycle area sign-off via live app boot and route check."""
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app, describe_startup_order


def test_lifecycle_area_signoff():
    assert describe_startup_order()[1] == "security_policy"
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
