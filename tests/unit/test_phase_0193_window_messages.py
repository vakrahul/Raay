"""PHASE 0193: sliding window preserves the system head."""
from src.orchestrator.brain.context_engine import AdvancedContextEngine
from src.providers.base import ChatMessage


def _msgs():
    return [
        ChatMessage(role="system", content="Rules."),
        ChatMessage(role="user", content="one"),
        ChatMessage(role="assistant", content="uno"),
        ChatMessage(role="user", content="two"),
        ChatMessage(role="assistant", content="dos"),
    ]


def test_window_keeps_head_and_newest():
    out = AdvancedContextEngine.window_messages(_msgs(), max_messages=3)
    assert [m.content for m in out] == ["Rules.", "two", "dos"]
    assert out[0].role == "system"
