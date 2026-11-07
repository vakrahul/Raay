"""PHASE 0102: shell spec declares project and verification contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_shell_spec_declares_contracts():
    text = (ROOT / "docs" / "WINUI_SHELL_SPEC.md").read_text(encoding="utf-8")
    for token in ("net8.0-windows", "UseWinUI", "check_client_shell.py", "PerMonitorV2"):
        assert token in text
