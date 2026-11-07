"""PHASE 0194: windowing edge cases fail safe."""
from src.orchestrator.brain.context_engine import AdvancedContextEngine
from src.providers.base import ChatMessage


def test_window_edge_cases():
    assert AdvancedContextEngine.window_messages([], max_messages=5) == []
    msgs = [ChatMessage(role="user", content="x")]
    assert AdvancedContextEngine.window_messages(msgs, max_messages=0) == []
    assert AdvancedContextEngine.window_messages(msgs, max_messages=-1) == []
    assert len(AdvancedContextEngine.window_messages(msgs, max_messages=10)) == 1
    two_sys = [ChatMessage(role="system", content="a"), ChatMessage(role="system", content="b")]
    assert len(AdvancedContextEngine.window_messages(two_sys, max_messages=5)) == 2
