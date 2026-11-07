"""PHASE 0056: config validators exist with documented bounds."""
import inspect

from src.orchestrator import config as config_mod
from src.orchestrator.config import AppConfig


def test_validators_present_with_documented_bounds():
    src = inspect.getsource(AppConfig)
    assert "validate_host_loopback" in src
    assert "canonicalize_paths" in src
    assert "1024" in src and "65535" in src
    assert hasattr(config_mod, "get_config")
