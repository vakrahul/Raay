"""PHASE 0087: shutdown spec declares binding and signal contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_shutdown_spec_declares_contracts():
    text = (ROOT / "docs" / "SHUTDOWN_SPEC.md").read_text(encoding="utf-8")
    for token in ("KeyboardInterrupt", "AppConfig", "log_level", "loopback"):
        assert token in text
