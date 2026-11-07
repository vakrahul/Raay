"""PHASE 0020: config area sign-off via live endpoint and spec presence."""
from pathlib import Path
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def test_config_area_signoff():
    assert (ROOT / "docs" / "CONFIG_SPEC.md").exists()
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        res = client.get("/api/v1/config")
        assert res.status_code == 200
        assert set(res.json()) == {
            "environment",
            "security_level",
            "workspace_root",
            "default_provider",
            "default_model",
        }
