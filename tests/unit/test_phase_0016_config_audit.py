"""PHASE 0016: config surface audit — env prefix, safe defaults, no secrets."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_config_uses_env_prefix_and_safe_defaults():
    text = (ROOT / "src" / "orchestrator" / "config.py").read_text(encoding="utf-8")
    assert 'env_prefix="WINAI_"' in text
    assert "127.0.0.1" in text
    assert "STRICT" in text
    for bad in ("sk-", "api_key=", "password=", "Bearer "):
        assert bad not in text, f"config.py must not embed secrets: {bad}"
