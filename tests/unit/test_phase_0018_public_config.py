"""PHASE 0018: public config subset helper excludes all secret material."""
from src.orchestrator.config import get_config


def test_public_config_dict_has_expected_keys_only():
    cfg = get_config()
    public = cfg.public_config_dict()
    assert set(public) == {
        "environment",
        "security_level",
        "workspace_root",
        "default_provider",
        "default_model",
    }
    blob = str(public).lower()
    for token in ("ipc_token", "api_key", "secret", "password"):
        assert token not in blob
