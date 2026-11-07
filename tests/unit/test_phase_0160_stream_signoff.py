"""PHASE 0160: streaming area sign-off with live mock collection."""
import asyncio

from src.providers.base import ChatMessage, collect_stream
from src.providers.mock_provider import MockProvider


def test_streaming_area_signoff():
    async def _run():
        provider = MockProvider()
        text = await collect_stream(provider, [ChatMessage(role="user", content="stream test")])
        assert len(text) > 20
        assert "stream test" in text or "Mock" in text or "response" in text

    asyncio.run(_run())
