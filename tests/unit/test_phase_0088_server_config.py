"""PHASE 0088: server config derives binding from validated AppConfig."""
from src.orchestrator.config import AppConfig
from run_vertical_slice import create_server_config


def test_server_config_uses_validated_binding():
    cfg = AppConfig(environment="testing", port=8899)
    server_config = create_server_config(cfg)
    assert server_config.host == "127.0.0.1"
    assert server_config.port == 8899
