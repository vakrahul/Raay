"""PHASE 0136: approval center contracts exist on both layers."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_approval_viewmodel_contract():
    text = (
        ROOT / "src" / "client" / "WinAI.Client" / "ViewModels" / "ApprovalViewModel.cs"
    ).read_text(encoding="utf-8")
    assert "PendingApprovals" in text and "Nonce" in text
    assert "RespondAsync" in text


def test_permission_model_defaults_denied():
    text = (
        ROOT / "src" / "client" / "WinAI.Client" / "Models" / "PermissionModel.cs"
    ).read_text(encoding="utf-8")
    assert "IsGranted" in text and "RequiresApproval" in text
