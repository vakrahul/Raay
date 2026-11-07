"""PHASE 0011: FastAPI entry point exposes the contracted route set."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_entry_script_wires_factory_and_loopback():
    text = (ROOT / "run_vertical_slice.py").read_text(encoding="utf-8")
    assert "create_app" in text and "get_config" in text
    assert "config.host" in text and "config.port" in text


def test_app_factory_registers_core_routes():
    text = (ROOT / "src" / "orchestrator" / "main.py").read_text(encoding="utf-8")
    for route in (
        '"/health"',
        '"/dashboard"',
        '"/api/v1/system/apps"',
        '"/api/v1/tasks/dispatch"',
        '"/api/v1/chat"',
        '"/api/v1/approval/respond"',
        '"/ws/v1/stream"',
    ):
        assert route in text, f"missing route registration: {route}"
