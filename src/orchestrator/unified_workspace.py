"""Unified AI Workspace Integration (Phases 89-90)."""
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.orchestrator.brain.brain_subsystem import BrainSubsystem
from src.orchestrator.custom_tools import CustomToolRegistry
from src.orchestrator.model_router import ModelRouter
from src.orchestrator.planner.coordinator import TaskCoordinator
from src.orchestrator.workspace_ingestion import WorkspaceIngestionService
from src.providers.base import ChatMessage, ProviderResponse
from src.providers.registry import ProviderRegistry
from src.security.policy_engine import ActionEvaluationResult, SecurityPolicyEngine


class UserPreferences(BaseModel):
    preferred_provider: str = "mock"
    privacy_mode: bool = False
    strict_approvals: bool = True
    theme: str = "Dark"


class UnifiedWorkspace:
    """Coordinates all subsystems into an integrated AI-native desktop workspace."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.preferences = UserPreferences()

        # Initialize core components
        self.provider_registry = ProviderRegistry()
        self.router = ModelRouter(self.provider_registry)
        self.brain = BrainSubsystem(workspace_root=self.workspace_root)
        self.policy_engine = SecurityPolicyEngine(
            workspace_root=self.workspace_root,
            require_approvals=self.preferences.strict_approvals,
        )
        self.coordinator = TaskCoordinator()
        self.ingestion = WorkspaceIngestionService(workspace_root=self.workspace_root)
        self.custom_tools = CustomToolRegistry()

        # Conversation history
        self.history: List[ChatMessage] = []

    async def send_user_message(self, text: str) -> str:
        """Send message, route to provider with prioritized memory context, and append response."""
        self.history.append(ChatMessage(role="user", content=text))

        # 1. Retrieve prioritized context from brain
        context = self.brain.assemble_context(query=text, max_tokens=2048)

        # 2. Route request
        route = self.router.route_request(task_description=text, privacy_mode=self.preferences.privacy_mode)
        provider = self.provider_registry.get_provider(route.selected_provider_id)

        # 3. Context-preserving message assembly
        assembled_messages = [
            ChatMessage(role="system", content=f"Context from Brain:\n{context}"),
        ] + self.history

        response = await provider.complete(messages=assembled_messages)
        self.history.append(ChatMessage(role="assistant", content=response.content))

        # Record observation in working memory
        self.brain.working.record_observation(f"User asked: '{text}'; Model responded.")
        return response.content
