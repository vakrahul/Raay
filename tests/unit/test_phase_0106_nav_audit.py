"""PHASE 0106: navigation destinations exist in the shell XAML."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
XAML = ROOT / "src" / "client" / "WinAI.Client" / "Views" / "MainWindow.xaml"


def test_nav_destinations_present():
    text = XAML.read_text(encoding="utf-8")
    for tag in ('Tag="Chat"', 'Tag="Models"', 'Tag="Agents"', 'Tag="Security"', 'Tag="Memory"'):
        assert tag in text
    assert "ContentFrame" in text and "EMERGENCY STOP" in text
