"""JEV memory and context integration (Stage 7).

JEV decisions receive the minimum relevant context only. Sensitive
material (keys, passwords, cookies, credentials, unrelated memories,
project directories, file contents) is dropped or redacted before it
can reach a decision provider. Decision history lives in an ephemeral,
bounded, in-memory log that is strictly separate from persistent user
memories — nothing here is ever written to BrainSubsystem stores, and
inferred preferences are never promoted to permanent facts.
"""

import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.security.audit_logger import redact_secrets

SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "token",
    "cookie",
    "credential",
    "private_key",
    "auth",
    "session_key",
)

ALLOWED_CONTEXT_KEYS = (
    "task_description",
    "task_type",
    "candidates",
)


class JevContextPack(BaseModel):
    """Minimum viable context for one JEV decision."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    task_description: str = Field(min_length=1, max_length=2000)
    task_type: str = "general"
    candidates: List[str] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(frag in lowered for frag in SENSITIVE_KEY_FRAGMENTS)


def filter_allowed_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only allowlisted keys; drop sensitive keys; redact all values."""
    packed: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in ALLOWED_CONTEXT_KEYS:
            continue
        if _is_sensitive_key(key):
            continue
        packed[key] = redact_secrets(value)
    return packed


def pack_decision_context(
    task_description: str,
    candidates: Optional[List[str]] = None,
    task_type: str = "general",
    max_chars: int = 500,
    extra: Optional[Dict[str, Any]] = None,
) -> JevContextPack:
    """Build a minimized, redacted context pack for one decision.

    Long descriptions are truncated, secret patterns redacted, and any
    caller-supplied extra fields pass through the allowlist filter.
    """
    redacted = str(redact_secrets(task_description.strip()))
    if len(redacted) > max_chars:
        redacted = redacted[:max_chars] + "…"
    fields: Dict[str, Any] = {
        "task_description": redacted,
        "task_type": task_type,
        "candidates": list(candidates or []),
    }
    if extra:
        filtered = filter_allowed_fields(extra)
        # Explicit arguments always win over untrusted extras.
        for key in ("task_description", "task_type", "candidates"):
            filtered.pop(key, None)
        fields.update(filtered)
    provenance = {
        "source": "jev_context_pack",
        "timestamp": time.time(),
        "truncated": len(task_description.strip()) > max_chars,
    }
    return JevContextPack(
        task_description=fields["task_description"],
        task_type=fields["task_type"],
        candidates=fields["candidates"],
        provenance=provenance,
    )


class JevDecisionLogEntry(BaseModel):
    """One recorded JEV decision with provenance. Ephemeral only."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    kind: str
    selected: str
    confidence: float
    provider_name: str
    timestamp: float = Field(default_factory=time.time)


class JevDecisionLog:
    """Bounded in-memory decision history.

    Intentionally NOT persistent: entries live for the process lifetime
    only and are never merged into BrainSubsystem user memories.
    """

    def __init__(self, max_entries: int = 200):
        self._entries: deque = deque(maxlen=max_entries)

    def record(
        self,
        request_id: str,
        kind: str,
        selected: str,
        confidence: float,
        provider_name: str,
    ) -> JevDecisionLogEntry:
        entry = JevDecisionLogEntry(
            request_id=request_id,
            kind=kind,
            selected=selected,
            confidence=confidence,
            provider_name=provider_name,
        )
        self._entries.append(entry)
        return entry

    def recent(self, limit: int = 10) -> List[JevDecisionLogEntry]:
        items = list(self._entries)
        return items[-limit:] if limit > 0 else []

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
