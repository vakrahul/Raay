"""PHASE 0101: WinUI shell project settings are present and pinned."""
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "src" / "client" / "WinAI.Client"


def test_csproj_targets_winui_net8():
    tree = ET.parse(CLIENT / "WinAI.Client.csproj")
    text = ET.tostring(tree.getroot(), encoding="unicode")
    assert "net8.0-windows" in text
    assert "UseWinUI" in text
    assert "Microsoft.WindowsAppSDK" in text


def test_manifest_declares_win10_11_and_dpi():
    text = (CLIENT / "app.manifest").read_text(encoding="utf-8")
    assert "8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a" in text
    assert "PerMonitorV2" in text
