"""Stage 9: JEV service state, endpoints, and dashboard panel."""
import asyncio

from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig
from src.orchestrator.jev.service import JevService
from src.orchestrator.main import create_app


def test_service_status_labels_mock():
    async def _run():
        svc = JevService()
        status = await svc.status_dict()
        assert status["enabled"] is True
        assert status["provider_name"] == "mock-jev"
        assert status["is_mock"] is True
        assert status["mock_warning"] is not None
        assert status["healthy"] is True
        assert "task_classification" in status["capabilities"]

    asyncio.run(_run())


def test_service_toggle_disables_traffic():
    async def _run():
        svc = JevService()
        assert svc.set_enabled(False) is False
        out = await svc.test_decision("Hello.")
        assert out["executed"] is False

    asyncio.run(_run())


def test_service_test_decision_records_metrics_and_log():
    async def _run():
        svc = JevService()
        out = await svc.test_decision("Refactor the authentication architecture.")
        assert out["executed"] is True
        assert out["is_mock"] is True
        assert out["mock_warning"] is not None
        assert svc.metrics_dict()["usage"]["mock-jev"]["requests"] == 1
        assert len(svc.recent_decisions()) == 1

    asyncio.run(_run())


def test_dashboard_does_not_expose_jev_mock_panel():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        html = client.get("/dashboard").text
        assert "jevCard" not in html
        assert "MOCK MODE" not in html
        assert "testJevDecision" not in html


def test_jev_endpoints_roundtrip():
    app = create_app(AppConfig(environment="testing"))
    with TestClient(app) as client:
        status = client.get("/api/v1/jev/status").json()
        assert status["is_mock"] is True and status["enabled"] is True

        assert client.post("/api/v1/jev/config", json={"enabled": False}).json() == {"enabled": False}
        assert client.get("/api/v1/jev/status").json()["enabled"] is False
        skipped = client.post("/api/v1/jev/test", json={"context": "Hi."}).json()
        assert skipped["executed"] is False

        assert client.post("/api/v1/jev/config", json={"enabled": True}).json() == {"enabled": True}
        result = client.post("/api/v1/jev/test", json={"context": "Refactor the API."}).json()
        assert result["executed"] is True
        assert result["is_mock"] is True

        metrics = client.get("/api/v1/jev/metrics").json()
        assert metrics["usage"]["mock-jev"]["requests"] == 1
        decisions = client.get("/api/v1/jev/decisions?limit=5").json()["decisions"]
        assert len(decisions) == 1 and decisions[0]["selected"]
