"""PHASE 0089: server config refuses non-loopback bindings fail-closed."""
import pytest
from pydantic import ValidationError

from src.orchestrator.config import AppConfig
from run_vertical_slice import create_server_config


def test_server_config_rejects_non_loopback():
    with pytest.raises(ValidationError):
        AppConfig(host="0.0.0.0")
    cfg = AppConfig(environment="testing")
    assert create_server_config(cfg).host in {"127.0.0.1", "localhost", "::1"}
