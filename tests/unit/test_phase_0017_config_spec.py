"""PHASE 0017: config spec declares precedence, defaults, and secrecy."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_config_spec_declares_contract():
    text = (ROOT / "docs" / "CONFIG_SPEC.md").read_text(encoding="utf-8")
    for token in ("WINAI_", "127.0.0.1", "strict", "ipc_token"):
        assert token in text
