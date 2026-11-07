"""Intelligent, provider-independent model router (Module 2).

Routes tasks based on complexity, capabilities, cost, latency, and privacy policies.
Enforces dynamic fallback chains when providers experience outages.
"""

from enum import Enum
import time
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.providers.base import BaseModelProvider, ChatMessage, ProviderResponse
from src.providers.registry import ProviderRegistry


class TaskComplexity(str, Enum):
    SIMPLE = "SIMPLE"       # Classification, extraction, simple Q&A
    MODERATE = "MODERATE"   # Summarization, single-file editing, planning
    COMPLEX = "COMPLEX"     # Multi-file refactoring, deep reasoning, architecture


class RouterMetrics(BaseModel):
    model_name: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    avg_latency_ms: float = 0.0
    quality_score: float = 1.0  # 0.0 to 1.0 based on test passes and user feedback


class IntelligentRouter:
    """Dynamic multi-factor model router with privacy guardrails and fallback cascading."""

    def __init__(self, registry: ProviderRegistry):
        self.registry = registry
        self.metrics: Dict[str, RouterMetrics] = {}
        # Fallback hierarchy: primary -> secondary -> local safe mode
        self.fallback_chain: List[str] = ["gemini", "openai", "local", "mock"]

    def assess_complexity(self, messages: List[ChatMessage]) -> TaskComplexity:
        """Heuristic task complexity classifier."""
        last_msg = next((m.content.lower() for m in reversed(messages) if m.role == "user"), "")
        total_tokens_approx = sum(len(m.content) // 4 for m in messages)

        # Complex indicators
        complex_keywords = ["refactor", "architecture", "debug", "security audit", "optimize", "implement system"]
        if any(k in last_msg for k in complex_keywords) or total_tokens_approx > 3000:
            return TaskComplexity.COMPLEX

        # Moderate indicators
        mod_keywords = ["write code", "test", "explain", "analyze", "create file", "review"]
        if any(k in last_msg for k in mod_keywords) or total_tokens_approx > 1000:
            return TaskComplexity.MODERATE

        return TaskComplexity.SIMPLE

    def select_best_model(
        self,
        messages: List[ChatMessage],
        privacy_mode: bool = False,
        require_tools: bool = False,
        require_vision: bool = False,
    ) -> str:
        """Select optimal provider ID adhering strictly to privacy and capability requirements."""
        # 1. Privacy Firewall: Zero external cloud data egress if privacy_mode is active
        if privacy_mode:
            if "local" in self.registry._providers:
                return "local"
            return "mock"

        complexity = self.assess_complexity(messages)

        # 2. Match complexity with model tier
        candidate_ids = []
        if complexity == TaskComplexity.SIMPLE:
            # Prefer fast, cost-effective models (Gemini Flash-Lite, Mock)
            candidate_ids = ["gemini", "mock", "local"]
        elif complexity == TaskComplexity.MODERATE:
            candidate_ids = ["gemini", "openai", "claude", "mock"]
        else: # COMPLEX
            # Prefer high-reasoning models
            candidate_ids = ["openai", "claude", "gemini", "mock"]

        # Filter candidates by registration and capabilities
        for pid in candidate_ids:
            if pid in self.registry._providers:
                prov = self.registry.get_provider(pid)
                caps = prov.get_capabilities()
                if require_tools and not caps.supports_tools:
                    continue
                if require_vision and not caps.supports_vision:
                    continue
                # Check circuit breaker if adapter has one
                cb = getattr(prov, "circuit_breaker", None)
                if cb and not cb.allow_request():
                    continue
                return pid

        return "mock"

    async def execute_with_fallback(
        self,
        messages: List[ChatMessage],
        privacy_mode: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> Tuple[ProviderResponse, str, float]:
        """Execute request with automatic controlled fallback cascade if primary fails."""
        primary_pid = self.select_best_model(
            messages=messages,
            privacy_mode=privacy_mode,
            require_tools=bool(tools),
        )

        # Build candidate fallback sequence starting with primary
        if privacy_mode:
            chain = [primary_pid]
        else:
            chain = [primary_pid] + [pid for pid in self.fallback_chain if pid != primary_pid]

        last_error = None
        for pid in chain:
            if pid not in self.registry._providers:
                continue

            provider = self.registry.get_provider(pid)
            model_name = provider.get_capabilities().model_name
            start_time = time.perf_counter()

            try:
                response = await provider.complete(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                latency_ms = (time.perf_counter() - start_time) * 1000
                self._record_telemetry(model_name, success=True, latency_ms=latency_ms)
                return response, pid, latency_ms

            except Exception as e:
                latency_ms = (time.perf_counter() - start_time) * 1000
                self._record_telemetry(model_name, success=False, latency_ms=latency_ms)
                last_error = e
                # Fallback to next provider in chain
                continue

        raise RuntimeError(f"All providers in fallback chain failed. Last error: {last_error}")

    def _record_telemetry(self, model_name: str, success: bool, latency_ms: float) -> None:
        m = self.metrics.setdefault(model_name, RouterMetrics(model_name=model_name))
        m.total_calls += 1
        if success:
            m.successful_calls += 1
            # Running average latency
            m.avg_latency_ms = (m.avg_latency_ms * (m.successful_calls - 1) + latency_ms) / m.successful_calls
        else:
            m.failed_calls += 1

    def record_quality_feedback(self, model_name: str, passed_tests: bool) -> None:
        """Update quality score based on test execution outcomes."""
        m = self.metrics.setdefault(model_name, RouterMetrics(model_name=model_name))
        adjustment = 0.05 if passed_tests else -0.10
        m.quality_score = max(0.1, min(1.0, m.quality_score + adjustment))
