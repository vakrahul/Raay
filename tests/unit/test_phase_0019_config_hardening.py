"""PHASE 0019: config hardening — hostile env values fail closed."""
import pytest
from pydantic import ValidationError

from src.orchestrator.config import AppConfig


def test_host_allowlist_rejects_spoofed_loopback():
    for hostile in ("0.0.0.0", "127.0.0.1.evil.com", "localhost.evil.com", ""):
        with pytest.raises(ValidationError):
            AppConfig(host=hostile)


def test_public_dict_stable_across_instances():
    a = AppConfig(environment="testing")
    b = AppConfig(environment="testing")
    assert set(a.public_config_dict()) == set(b.public_config_dict())
    assert "ipc_token" not in a.public_config_dict()
