"""Model routing, context-preserving model switching, and cross-model collaboration (Phases 81-83)."""
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel
from src.providers.base import BaseModelProvider, ChatMessage, ProviderResponse
from src.providers.registry import ProviderRegistry


class RoutingDecision(BaseModel):
    selected_provider_id: str
    selected_model: str
    rationale: str


class ModelRouter:
    """Intelligent router selecting models based on privacy, task complexity, and context window."""

    def __init__(self, registry: ProviderRegistry):
        self.registry = registry

    def route_request(self, task_description: str, privacy_mode: bool = False) -> RoutingDecision:
        """Select appropriate provider based on task traits and user privacy preference."""
        if privacy_mode:
            return RoutingDecision(
                selected_provider_id="local",
                selected_model="llama3.2:latest",
                rationale="Local model selected to enforce strict data sovereignty (zero cloud egress).",
            )

        task_lower = task_description.lower()
        if any(term in task_lower for term in ["refactor", "code", "debug", "architecture", "complex"]):
            # Prefer high-capability reasoning model
            return RoutingDecision(
                selected_provider_id="mock",
                selected_model="mock-gpt-4o",
                rationale="High-capability reasoning model selected for software engineering task.",
            )

        return RoutingDecision(
            selected_provider_id="mock",
            selected_model="mock-gpt-4o",
            rationale="Default general-purpose model selected.",
        )

    def switch_model_preserve_context(
        self,
        messages: List[ChatMessage],
        target_context_limit_tokens: int = 4096,
    ) -> List[ChatMessage]:
        """Compress or window conversation messages to fit inside target model context window."""
        # Simple heuristic: ~4 characters per token
        max_chars = target_context_limit_tokens * 4
        total_chars = sum(len(m.content) for m in messages)

        if total_chars <= max_chars:
            return messages

        # Preserve system message (if any) and newest messages
        system_msgs = [m for m in messages if m.role == "system"]
        conversation = [m for m in messages if m.role != "system"]

        preserved = []
        accumulated_chars = sum(len(m.content) for m in system_msgs)

        # Iterate from newest to oldest
        for m in reversed(conversation):
            if accumulated_chars + len(m.content) > max_chars:
                break
            preserved.append(m)
            accumulated_chars += len(m.content)

        return system_msgs + list(reversed(preserved))

    async def cross_model_review(
        self,
        proposer: BaseModelProvider,
        reviewer: BaseModelProvider,
        prompt: str,
    ) -> Tuple[ProviderResponse, ProviderResponse]:
        """Proposer proposes solution; reviewer critiques it.

        NOTE: Under Non-Negotiable Principle 3 & Phase 83, agreement between models
        NEVER constitutes a security authorization. All actions still require policy check.
        """
        # 1. Proposer draft
        proposer_resp = await proposer.complete(
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=0.7,
        )

        # 2. Reviewer critique
        review_prompt = (
            f"Critique and review the following proposed solution to: '{prompt}':\n\n"
            f"{proposer_resp.content}\n\n"
            f"Provide an objective review and highlight any potential bugs or missing edge cases."
        )
        reviewer_resp = await reviewer.complete(
            messages=[ChatMessage(role="user", content=review_prompt)],
            temperature=0.2,
        )

        return proposer_resp, reviewer_resp
