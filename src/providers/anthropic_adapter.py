"""Official Anthropic Claude Provider Adapter (Zone 4 boundary)."""
import json
from typing import Any, AsyncIterator, Dict, List, Optional
import httpx

from src.providers.base import (
    BaseModelProvider,
    ChatMessage,
    ModelCapabilities,
    ProviderResponse,
    ToolCallProposal,
)
from src.providers.resilience import CircuitBreaker, retry_with_backoff


class AnthropicAdapter(BaseModelProvider):
    """Adapter for official Anthropic Messages REST API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "claude-3-5-sonnet-20241022",
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 60.0,
    ):
        self.api_key = api_key or ""
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.circuit_breaker = CircuitBreaker()
        self.capabilities = ModelCapabilities(
            provider_name="anthropic",
            model_name=model_name,
            context_window=200000,
            supports_streaming=True,
            supports_tools=True,
            supports_vision=True,
        )

    def get_capabilities(self) -> ModelCapabilities:
        return self.capabilities

    async def health_check(self) -> bool:
        return bool(self.api_key)

    async def complete(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> ProviderResponse:
        if not self.circuit_breaker.allow_request():
            raise RuntimeError("Anthropic provider circuit breaker is OPEN")

        system_prompt = ""
        anthropic_msgs = []
        for m in messages:
            if m.role == "system":
                system_prompt += m.content + "\n"
            else:
                anthropic_msgs.append({"role": m.role, "content": m.content})

        payload = {
            "model": self.model_name,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt.strip()

        async def _call():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(
                    f"{self.base_url}/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                res.raise_for_status()
                return res.json()

        try:
            data = await retry_with_backoff(_call)
            self.circuit_breaker.record_success()
            content_text = ""
            tool_calls = []
            for block in data.get("content", []):
                if block.get("type") == "text":
                    content_text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        ToolCallProposal(
                            id=block["id"],
                            tool_name=block["name"],
                            arguments=block.get("input", {}),
                        )
                    )

            return ProviderResponse(
                content=content_text,
                model_name=self.model_name,
                tool_calls=tool_calls,
                finish_reason=data.get("stop_reason", "end_turn"),
                usage=data.get("usage", {}),
            )
        except Exception as e:
            self.circuit_breaker.record_failure()
            raise e

    async def stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        # Anthropic streaming protocol parser
        anthropic_msgs = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        payload = {
            "model": self.model_name,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            event_data = json.loads(line[6:])
                            if event_data.get("type") == "content_block_delta":
                                delta = event_data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield delta.get("text", "")
                        except json.JSONDecodeError:
                            continue
