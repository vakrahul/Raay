"""Official Google Gemini Provider Adapter (Zone 4 boundary)."""
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


class GeminiAdapter(BaseModelProvider):
    """Adapter for Google Gemini REST API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.5-flash-lite",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 60.0,
    ):
        self.api_key = api_key or ""
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.circuit_breaker = CircuitBreaker()
        self.capabilities = ModelCapabilities(
            provider_name="gemini",
            model_name=model_name,
            context_window=1000000,
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
            raise RuntimeError("Gemini provider circuit breaker is OPEN")

        contents = []
        sys_parts = []
        for m in messages:
            if m.role == "system":
                sys_parts.append({"text": m.content})
            else:
                role = "user" if m.role == "user" else "model"
                contents.append({"role": role, "parts": [{"text": m.content}]})

        if not contents:
            contents.append({"role": "user", "parts": [{"text": "Hello"}]})

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if sys_parts:
            payload["systemInstruction"] = {"parts": sys_parts}

        url = f"{self.base_url}/models/{self.model_name}:generateContent?key={self.api_key}"

        async def _call():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(url, json=payload)
                res.raise_for_status()
                return res.json()

        try:
            data = await retry_with_backoff(_call)
            self.circuit_breaker.record_success()
            candidates = data.get("candidates", [])
            text = ""
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for p in parts:
                    if "text" in p:
                        text += p["text"]

            return ProviderResponse(
                content=text,
                model_name=self.model_name,
                tool_calls=[],
                finish_reason="stop",
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
        # Fallback to single completion chunk for Gemini stream simulation
        res = await self.complete(messages, tools, temperature, max_tokens)
        yield res.content
