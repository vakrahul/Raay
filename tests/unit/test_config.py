"""Unit tests for configuration management."""
import pytest
from pathlib import Path
from pydantic import ValidationError
from src.orchestrator.config import AppConfig, EnvironmentType, SecurityLevel, get_config

@pytest.mark.unit
def test_default_config():
    """Verify safe default settings."""
    cfg = get_config()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    assert cfg.security_level == SecurityLevel.STRICT
    assert len(cfg.ipc_token) == 64  # 32 bytes in hex
    assert cfg.subprocess_timeout_seconds == 60
    assert cfg.subprocess_max_memory_mb == 1024
    assert cfg.subprocess_max_output_bytes == 51200
    assert cfg.default_provider == "mock"

@pytest.mark.unit
def test_host_loopback_enforcement():
    """Ensure binding to non-loopback addresses raises a Security violation."""
    with pytest.raises(ValidationError) as excinfo:
        AppConfig(host="0.0.0.0")
    assert "Security violation" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        AppConfig(host="192.168.1.100")
    assert "Security violation" in str(excinfo.value)

@pytest.mark.unit
def test_port_validation():
    """Ensure invalid ports below 1024 or above 65535 fail validation."""
    with pytest.raises(ValidationError):
        AppConfig(port=80)
    with pytest.raises(ValidationError):
        AppConfig(port=70000)

@pytest.mark.unit
def test_path_canonicalization(temp_workspace: Path):
    """Ensure directory paths are resolved to absolute canonical paths."""
    cfg = AppConfig(workspace_root=temp_workspace)
    assert cfg.workspace_root.is_absolute()
    assert cfg.workspace_root == temp_workspace.resolve()

@pytest.mark.unit
def test_env_prefix_override(monkeypatch, temp_workspace: Path):
    """Ensure settings can be loaded safely via WINAI_ environment variables."""
    monkeypatch.setenv("WINAI_PORT", "9000")
    monkeypatch.setenv("WINAI_SECURITY_LEVEL", "moderate")
    monkeypatch.setenv("WINAI_ENVIRONMENT", "testing")
    cfg = get_config()
    assert cfg.port == 9000
    assert cfg.security_level == SecurityLevel.MODERATE
    assert cfg.environment == EnvironmentType.TESTING
