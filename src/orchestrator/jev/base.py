"""Provider-neutral JEV decision abstraction (Stage 2).

JEV (TypeSafe AI System One decision model) is an advisory classifier only.
It must never grant permissions, bypass approvals, or override the
SecurityPolicyEngine. All responses are validated before use, and every
call has a bounded timeout with deterministic fallback.
"""

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JevDecisionKind(str, Enum):
    TASK_CLASSIFICATION = "task_classification"
    AGENT_SELECTION = "agent_selection"
    MODEL_SELECTION = "model_selection"
    TOOL_SELECTION = "tool_selection"
    WORKFLOW_BRANCH = "workflow_branch"
    GUARDRAIL_ASSESSMENT = "guardrail_assessment"
    CONTEXT_PRUNE = "context_prune"


class JevError(Exception):
    """Base class for JEV decision-layer failures."""


class JevTimeoutError(JevError):
    """Raised when a decision request exceeds its deadline."""


class JevUnavailableError(JevError):
    """Raised when no JEV provider can serve the request."""


class JevValidationError(JevError):
    """Raised when a decision response fails schema or sanity checks."""


class JevDecisionRequest(BaseModel):
    """Structured, validated input for a single JEV decision."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    kind: JevDecisionKind
    prompt_context: str = Field(min_length=1, max_length=8000)
    candidates: List[str] = Field(min_length=1, max_length=255)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=500, ge=50, le=10000)

    @field_validator("candidates")
    @classmethod
    def candidates_must_be_nonempty_strings(cls, v: List[str]) -> List[str]:
        for item in v:
            if not item or not item.strip():
                raise ValueError("candidates must be non-empty strings")
        if len(set(v)) != len(v):
            raise ValueError("candidates must be unique")
        return v


class JevDecisionResponse(BaseModel):
    """Structured, validated output of a single JEV decision."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    kind: JevDecisionKind
    selected: str
    probabilities: Dict[str, float]
    confidence: float = Field(ge=0.0, le=1.0)
    latency_ms: float = Field(ge=0.0)
    provider_name: str
    is_mock: bool = False


def validate_jev_response(
    response: JevDecisionResponse,
    request: JevDecisionRequest,
    tolerance: float = 0.05,
) -> JevDecisionResponse:
    """Validate a provider response against its request.

    Checks request-ID echo, kind echo, candidate membership, and that the
    probability distribution sums to ~1.0. Raises JevValidationError.
    """
    if response.request_id != request.request_id:
        raise JevValidationError(
            f"request_id mismatch: {response.request_id!r} != {request.request_id!r}"
        )
    if response.kind != request.kind:
        raise JevValidationError(
            f"kind mismatch: {response.kind!r} != {request.kind!r}"
        )
    if response.selected not in request.candidates:
        raise JevValidationError(
            f"selected {response.selected!r} not in request candidates"
        )
    unknown = set(response.probabilities) - set(request.candidates)
    if unknown:
        raise JevValidationError(f"probabilities contain unknown candidates: {unknown}")
    total = sum(response.probabilities.values())
    if abs(total - 1.0) > tolerance:
        raise JevValidationError(f"probabilities sum to {total}, expected ~1.0")
    if any(p < 0.0 or p > 1.0 for p in response.probabilities.values()):
        raise JevValidationError("probabilities must lie in [0.0, 1.0]")
    return response


class BaseJevProvider(ABC):
    """Abstract JEV decision provider. Never hard-codes credentials."""

    provider_name: str = "base-jev"
    is_mock: bool = False

    @abstractmethod
    async def decide(self, request: JevDecisionRequest) -> JevDecisionResponse:
        """Return a validated decision for the request."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True when the provider can serve decisions. Never raises."""
        raise NotImplementedError


async def decide_with_timeout(
    provider: BaseJevProvider,
    request: JevDecisionRequest,
    timeout_ms: Optional[int] = None,
) -> JevDecisionResponse:
    """Execute a decision with a bounded deadline and validate the result.

    Raises JevTimeoutError on deadline expiry (caller applies fallback),
    JevValidationError on malformed responses. Cancellation propagates.
    """
    deadline = (timeout_ms if timeout_ms is not None else request.timeout_ms) / 1000.0
    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(provider.decide(request), timeout=deadline)
    except asyncio.TimeoutError as e:
        raise JevTimeoutError(
            f"JEV provider '{provider.provider_name}' exceeded {deadline*1000:.0f}ms"
        ) from e
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if response.latency_ms == 0.0:
        response.latency_ms = elapsed_ms
    return validate_jev_response(response, request)
