"""PHASE 0161: cancellation primitives exist on client and server."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_cancel_primitives_present():
    vm = (
        ROOT / "src" / "client" / "WinAI.Client" / "ViewModels" / "ChatViewModel.cs"
    ).read_text(encoding="utf-8")
    assert "CancellationTokenSource" in vm and "Cancel" in vm
    main = (ROOT / "src" / "orchestrator" / "main.py").read_text(encoding="utf-8")
    assert '"cancelled"' in main
