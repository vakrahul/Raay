"""Official OpenAI Provider Adapter (Zone 4 boundary)."""
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


class OpenAIAdapter(BaseModelProvider):
    """Adapter for official OpenAI REST API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gpt-4o",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ):
        self.api_key = api_key or ""
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.circuit_breaker = CircuitBreaker()
        self.capabilities = ModelCapabilities(
            provider_name="openai",
            model_name=model_name,
            context_window=128000,
            supports_streaming=True,
            supports_tools=True,
            supports_vision=True,
        )

    def get_capabilities(self) -> ModelCapabilities:
        return self.capabilities

    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
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
        if not self.circuit_breaker.allow_request():
            raise RuntimeError("OpenAI provider circuit breaker is OPEN")

        payload = {
            "model": self.model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools

        async def _call():
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                res.raise_for_status()
                return res.json()

        try:
            data = await retry_with_backoff(_call)
            self.circuit_breaker.record_success()
            choice = data["choices"][0]["message"]
            content = choice.get("content") or ""
            tool_calls = []
            if "tool_calls" in choice and choice["tool_calls"]:
                for tc in choice["tool_calls"]:
                    fn = tc["function"]
                    args = json.loads(fn.get("arguments", "{}"))
                    tool_calls.append(
                        ToolCallProposal(
                            id=tc["id"],
                            tool_name=fn["name"],
                            arguments=args,
                        )
                    )
            return ProviderResponse(
                content=content,
                model_name=self.model_name,
                tool_calls=tool_calls,
                finish_reason=data["choices"][0].get("finish_reason", "stop"),
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
        payload = {
            "model": self.model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and not line.startswith("data: [DONE]"):
                        try:
                            chunk = json.loads(line[6:])
                            delta = chunk["choices"][0]["delta"]
                            if "content" in delta and delta["content"]:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            continue
