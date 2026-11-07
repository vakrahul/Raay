"""PHASE 0111: chat workspace files expose message, state, cancellation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VM = ROOT / "src" / "client" / "WinAI.Client" / "ViewModels" / "ChatViewModel.cs"
MODEL = ROOT / "src" / "client" / "WinAI.Client" / "Models" / "ChatMessageModel.cs"


def test_chat_workspace_contracts_present():
    vm = VM.read_text(encoding="utf-8")
    assert "IsGenerating" in vm and "CancellationTokenSource" in vm
    assert "Messages" in vm and "InputMessage" in vm
    model = MODEL.read_text(encoding="utf-8")
    assert "Role" in model and "Content" in model
