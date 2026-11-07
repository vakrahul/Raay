"""Deterministic Mock Provider for offline testing, local dev, and vertical slice."""
import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional
from src.providers.base import (
    BaseModelProvider,
    ChatMessage,
    ModelCapabilities,
    ProviderResponse,
    ToolCallProposal,
)


class MockProvider(BaseModelProvider):
    """Offline deterministic provider simulating LLM behaviors."""

    def __init__(self, model_name: str = "mock-gpt-4o"):
        self.model_name = model_name
        self.capabilities = ModelCapabilities(
            provider_name="mock",
            model_name=model_name,
            context_window=16384,
            supports_streaming=True,
            supports_tools=True,
            supports_vision=False,
        )

    def get_capabilities(self) -> ModelCapabilities:
        return self.capabilities

    async def health_check(self) -> bool:
        return True

    async def complete(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> ProviderResponse:
        last_msg = messages[-1].content if messages else ""
        
        # Check if message requests a tool action
        if "list files" in last_msg.lower():
            return ProviderResponse(
                content="I will list the files in your workspace.",
                model_name=self.model_name,
                tool_calls=[
                    ToolCallProposal(
                        id="call_mock_1",
                        tool_name="fs_list_files",
                        arguments={"path": "."},
                    )
                ],
            )

        # Approval-button demo trigger: proposes a harmless sandboxed command
        # so the strict policy engine issues a real approval nonce.
        if "run a command" in last_msg.lower():
            return ProviderResponse(
                content="I will run a harmless sandboxed echo command.",
                model_name=self.model_name,
                tool_calls=[
                    ToolCallProposal(
                        id="call_mock_2",
                        tool_name="terminal_run",
                        arguments={"command": ["echo", "approval-button-probe"]},
                    )
                ],
            )

        return ProviderResponse(
            content=f"Mock response to: '{last_msg}'",
            model_name=self.model_name,
            tool_calls=[],
        )

    async def stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        last_msg = messages[-1].content if messages else "Hello"
        tokens = [
            "This ", "is ", "a ", "secure ", "streamed ", "response ",
            "from ", "WinAI-OE ", "MockProvider: ", f"'{last_msg}'."
        ]
        for token in tokens:
            await asyncio.sleep(0.01)  # Simulate network latency
            yield token
