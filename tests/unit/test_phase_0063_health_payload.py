"""PHASE 0063: health payload builder degrades cleanly without secrets."""
from src.orchestrator.main import build_health_payload


def test_health_payload_healthy_and_degraded():
    healthy = build_health_payload(
        provider_healthy=True,
        app_name="WinAI",
        app_version="0.1.0",
        environment="testing",
        provider_name="mock",
        configured_providers=[],
    )
    assert healthy["status"] == "healthy"
    degraded = build_health_payload(
        provider_healthy=False,
        app_name="WinAI",
        app_version="0.1.0",
        environment="testing",
        provider_name="mock",
        configured_providers=[],
    )
    assert degraded["status"] == "degraded"
    assert "sk-" not in str(healthy).lower()
