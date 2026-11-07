"""PHASE 0181: approval dialog renders immutable action details."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
XAML = ROOT / "src" / "client" / "WinAI.Client" / "Views" / "ApprovalDialog.xaml"


def test_approval_dialog_contract():
    text = XAML.read_text(encoding="utf-8")
    for token in ("ToolName", "RiskTier", "TargetResource", "Reason", "Authorize Once", "Block Action"):
        assert token in text
    assert "Mode=OneWay" in text
    assert "Nonce" not in text
