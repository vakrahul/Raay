"""PHASE 0086: entry script serves validated loopback config via uvicorn."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_entry_uses_validated_config():
    text = (ROOT / "run_vertical_slice.py").read_text(encoding="utf-8")
    assert "get_config()" in text
    assert "create_server_config" in text
    assert "config.host" in text and "config.port" in text
    assert "uvicorn.Server" in text
