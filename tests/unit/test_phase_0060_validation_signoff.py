"""PHASE 0060: validation area sign-off via env override end to end."""
from src.orchestrator.config import get_config


def test_env_override_signoff(monkeypatch):
    monkeypatch.setenv("WINAI_ENVIRONMENT", "testing")
    monkeypatch.setenv("WINAI_PORT", "9876")
    cfg = get_config()
    assert cfg.environment.value == "testing"
    assert cfg.port == 9876
    assert cfg.host == "127.0.0.1"
