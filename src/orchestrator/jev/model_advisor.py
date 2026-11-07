"""JEV model-router integration advisor (Stage 5).

Sits alongside the existing IntelligentRouter without replacing it: JEV
suggests a route from a caller-approved allowlist, the suggestion is
validated, and any failure falls back to the caller's default (ideally
the legacy router's own pick). Privacy mode restricts candidates to
local/offline providers and truncates decision context so private
project data never reaches a new provider without authorization.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from src.orchestrator.jev.base import JevValidationError
from src.orchestrator.jev.router import JevDecisionRouter
from src.providers.base import ChatMessage
from src.providers.registry import ProviderRegistry

try:
    from src.orchestrator.intelligent_router import IntelligentRouter
except Exception:  # pragma: no cover - legacy router always present in practice
    IntelligentRouter = None  # type: ignore


class ModelRouteRecommendation(BaseModel):
    """Validated routing suggestion. Advisory only; caller executes."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    source: str  # "jev" or "fallback"
    confidence: float
    rationale: str
    privacy_enforced: bool = False
    latency_ms: float = 0.0


class JevModelAdvisor:
    """JEV-assisted routing constrained by allowlists and privacy mode."""

    # Provider IDs that never cause external data egress.
    OFFLINE_IDS = ("mock",)

    def __init__(
        self,
        router: JevDecisionRouter,
        registry: Optional[ProviderRegistry] = None,
        legacy_router: Optional["IntelligentRouter"] = None,
        context_budget_chars: int = 500,
    ):
        self.router = router
        self.registry = registry or ProviderRegistry()
        self.legacy_router = legacy_router
        self.context_budget_chars = context_budget_chars

    def minimize_context(self, task_description: str) -> str:
        """Truncate decision context so only the task gist is classified."""
        text = task_description.strip()
        if len(text) <= self.context_budget_chars:
            return text
        return text[: self.context_budget_chars] + "…"

    def _is_local(self, provider_id: str) -> bool:
        if provider_id in self.OFFLINE_IDS:
            return True
        try:
            caps = self.registry.get_provider(provider_id).get_capabilities()
        except KeyError:
            return False
        return caps.provider_name == "local"

    def _effective_fallback(
        self,
        fallback_provider_id: str,
        candidates: List[str],
        minimized: str,
        privacy_mode: bool,
        require_tools: bool,
    ) -> str:
        """Prefer the legacy router's pick when it stays inside the allowlist."""
        if self.legacy_router is None:
            return fallback_provider_id
        try:
            legacy_pick = self.legacy_router.select_best_model(
                [ChatMessage(role="user", content=minimized)],
                privacy_mode=privacy_mode,
                require_tools=require_tools,
            )
        except Exception:
            return fallback_provider_id
        return legacy_pick if legacy_pick in candidates else fallback_provider_id

    async def recommend(
        self,
        task_description: str,
        allowed_provider_ids: List[str],
        fallback_provider_id: str,
        privacy_mode: bool = False,
        require_tools: bool = False,
        use_legacy_fallback: bool = False,
    ) -> ModelRouteRecommendation:
        """Recommend one provider ID from the allowlist.

        Raises JevValidationError when the allowlist or fallback violates
        registration or privacy constraints (fail closed, never leak).
        """
        registered = [pid for pid in allowed_provider_ids if pid in self.registry._providers]
        if not registered:
            raise JevValidationError("no allowed providers are registered")
        if fallback_provider_id not in self.registry._providers:
            raise JevValidationError(f"fallback provider '{fallback_provider_id}' is not registered")

        candidates = registered
        if require_tools:
            candidates = [
                pid for pid in candidates
                if self.registry.get_provider(pid).get_capabilities().supports_tools
            ]
            if not candidates:
                raise JevValidationError("no allowed providers support tools")
        if privacy_mode:
            candidates = [pid for pid in candidates if self._is_local(pid)]
            if not candidates:
                raise JevValidationError("privacy mode requires a local/offline provider")
            if not self._is_local(fallback_provider_id):
                raise JevValidationError("privacy mode requires a local/offline fallback")

        minimized = self.minimize_context(task_description)
        effective_fallback = fallback_provider_id
        if use_legacy_fallback:
            effective_fallback = self._effective_fallback(
                fallback_provider_id, candidates, minimized, privacy_mode, require_tools
            )

        outcome = await self.router.select_model(minimized, candidates, effective_fallback)
        selected = outcome.selected if outcome.selected in candidates else effective_fallback
        source = outcome.source if outcome.selected in candidates else "fallback"

        if privacy_mode and not self._is_local(selected):
            selected, source = effective_fallback, "fallback"  # belt-and-braces

        rationale = (
            f"{'JEV' if source == 'jev' else 'Fallback'} route to '{selected}' "
            f"(confidence {outcome.confidence:.2f}); privacy_mode={privacy_mode}; "
            f"allowlist={candidates}; {outcome.reason}"
        )
        return ModelRouteRecommendation(
            provider_id=selected,
            source=source,
            confidence=outcome.confidence if source == "jev" else 0.0,
            rationale=rationale,
            privacy_enforced=privacy_mode,
            latency_ms=outcome.latency_ms,
        )

    def metrics(self) -> Dict[str, object]:
        return self.router.metrics()
