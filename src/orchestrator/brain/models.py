"""Data models for Contextual Super Brain (Stage IV)."""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    WORKING = "WORKING"
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PROJECT = "PROJECT"


class VerificationStatus(str, Enum):
    VERIFIED_OBSERVATION = "VERIFIED_OBSERVATION" # Output from a verified tool
    USER_ASSERTED = "USER_ASSERTED"               # Directly declared by user
    INFERRED = "INFERRED"                         # Inferred by LLM reasoning
    OUTDATED = "OUTDATED"                         # Flagged as superseded or invalid


class MemoryEntry(BaseModel):
    """Atomic memory unit stored in the Contextual Brain."""
    id: str
    memory_type: MemoryType
    content: str
    verification_status: VerificationStatus = VerificationStatus.USER_ASSERTED
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
