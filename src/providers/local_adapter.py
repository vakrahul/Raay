"""Local Model Adapter for Ollama / LM Studio / Local Inference Endpoints."""
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
from src.providers.resilience import CircuitBreaker


class LocalModelAdapter(BaseModelProvider):
    """Adapter for local inference servers like Ollama or vLLM."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model_name: str = "llama3.2:latest",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=15.0)
        self.capabilities = ModelCapabilities(
            provider_name="local",
            model_name=model_name,
            context_window=8192,
            supports_streaming=True,
            supports_tools=True,
            supports_vision=False,
        )

    def get_capabilities(self) -> ModelCapabilities:
        return self.capabilities

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{self.base_url}/api/tags")
                return res.status_code == 200
        except Exception:
            return False

    async def complete(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> ProviderResponse:
        payload = {
            "model": self.model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(f"{self.base_url}/api/chat", json=payload)
            res.raise_for_status()
            data = res.json()
            msg = data.get("message", {})
            return ProviderResponse(
                content=msg.get("content", ""),
                model_name=self.model_name,
                tool_calls=[],
                finish_reason="stop",
            )

    async def stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {"temperature": temperature},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.strip():
                        try:
                            chunk = json.loads(line)
                            delta = chunk.get("message", {}).get("content", "")
                            if delta:
                                yield delta
                        except json.JSONDecodeError:
                            continue
