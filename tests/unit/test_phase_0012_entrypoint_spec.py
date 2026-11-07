"""PHASE 0012: entry-point spec exists and loopback binding is enforced."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_entrypoint_spec_declares_contracts():
    text = (ROOT / "docs" / "ENTRYPOINT_SPEC.md").read_text(encoding="utf-8")
    for token in ("run_vertical_slice.py", "create_app", "/ws/v1/stream", "127.0.0.1"):
        assert token in text


def test_config_defaults_to_loopback():
    text = (ROOT / "src" / "orchestrator" / "config.py").read_text(encoding="utf-8")
    assert "127.0.0.1" in text
    assert "0.0.0.0" not in text
