"""PHASE 0159: collector handles empty streams and cancellation."""
import asyncio

from src.providers.base import BaseModelProvider, ChatMessage, ModelCapabilities, collect_stream


class _EmptyStreamProvider(BaseModelProvider):
    async def complete(self, messages, tools=None, temperature=0.7, max_tokens=1024):
        from src.providers.base import ProviderResponse

        return ProviderResponse(content="", model_name="empty")

    async def stream(self, messages, tools=None, temperature=0.7, max_tokens=1024):
        if False:
            yield ""
        return

    def get_capabilities(self):
        return ModelCapabilities(provider_name="empty", model_name="empty")

    async def health_check(self):
        return True


class _SlowStreamProvider(_EmptyStreamProvider):
    async def stream(self, messages, tools=None, temperature=0.7, max_tokens=1024):
        await asyncio.sleep(30)
        yield "late"


def test_empty_stream_collects_empty():
    async def _run():
        return await collect_stream(_EmptyStreamProvider(), [ChatMessage(role="user", content="hi")])

    assert asyncio.run(_run()) == ""


def test_cancelled_stream_propagates():
    async def _run():
        task = asyncio.create_task(
            collect_stream(_SlowStreamProvider(), [ChatMessage(role="user", content="hi")])
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "completed"

    assert asyncio.run(_run()) == "cancelled"
