"""PHASE 0146: baseline accessibility markers exist in shell files."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_baseline_a11y_markers_present():
    manifest = (ROOT / "src" / "client" / "WinAI.Client" / "app.manifest").read_text(encoding="utf-8")
    assert "PerMonitorV2" in manifest
    main_xaml = (
        ROOT / "src" / "client" / "WinAI.Client" / "Views" / "MainWindow.xaml"
    ).read_text(encoding="utf-8")
    assert "AutomationProperties.Name" in main_xaml
