"""PHASE 0195: windowing area sign-off with token-budget fit."""
from src.orchestrator.brain.context_engine import AdvancedContextEngine
from src.providers.base import ChatMessage


def test_window_signoff_fits_budget():
    msgs = [ChatMessage(role="system", content="Rules.")] + [
        ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"turn number {i} here")
        for i in range(30)
    ]
    windowed = AdvancedContextEngine.window_messages(msgs, max_messages=9)
    assert len(windowed) == 9
    assert windowed[0].role == "system"
    est_tokens = sum(max(1, len(m.content) // 4) for m in windowed)
    assert est_tokens < 2048
