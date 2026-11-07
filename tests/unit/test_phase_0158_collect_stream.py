"""PHASE 0158: stream collector concatenates chunks with truncation."""
import asyncio

from src.providers.base import ChatMessage, collect_stream
from src.providers.mock_provider import MockProvider


def test_collect_stream_concatenates():
    async def _run():
        return await collect_stream(
            MockProvider(), [ChatMessage(role="user", content="hi")]
        )

    text = asyncio.run(_run())
    assert len(text) > 0


def test_collect_stream_truncates():
    async def _run():
        return await collect_stream(
            MockProvider(), [ChatMessage(role="user", content="hi")], max_chars=10
        )

    assert len(asyncio.run(_run())) == 10
