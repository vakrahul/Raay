"""Privacy, data retention, and transparency disclosure engine (Phase 96)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import time
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class DataRetentionPolicy(BaseModel):
    retain_logs_days: int = Field(default=30, ge=1, le=365)
    retain_episodic_memory_days: int = Field(default=90, ge=1, le=365)
    purge_working_memory_on_exit: bool = True


class PrivacyDisclosure(BaseModel):
    provider_type: str  # "LOCAL" or "CLOUD"
    data_leaves_machine: bool
    destination_endpoint: str
    encrypted_in_transit: bool
    retention_warning: Optional[str] = None


class PrivacyController:
    """Enforces privacy policies, retention rules, and data sovereignty disclosures."""

    def __init__(self, db_path: Path, log_dir: Path, policy: Optional[DataRetentionPolicy] = None):
        self.db_path = db_path.resolve()
        self.log_dir = log_dir.resolve()
        self.policy = policy or DataRetentionPolicy()

    def get_disclosure(self, provider_id: str, endpoint: str) -> PrivacyDisclosure:
        """Provide transparent user disclosure of data egress before prompt dispatch."""
        is_local = provider_id in ["local", "ollama", "mock"] or "127.0.0.1" in endpoint or "localhost" in endpoint
        return PrivacyDisclosure(
            provider_type="LOCAL" if is_local else "CLOUD",
            data_leaves_machine=not is_local,
            destination_endpoint=endpoint,
            encrypted_in_transit=endpoint.startswith("https://") or is_local,
            retention_warning=None if is_local else "Prompt data will be processed by third-party cloud infrastructure according to provider policy.",
        )

    def purge_expired_records(self) -> Dict[str, int]:
        """Purge episodic memories older than retention policy."""
        purged_memories = 0
        if self.db_path.exists():
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=self.policy.retain_episodic_memory_days)).isoformat()
            with sqlite3.connect(str(self.db_path)) as conn:
                cur = conn.execute("DELETE FROM memories WHERE created_at < ?;", (cutoff_date,))
                purged_memories = cur.rowcount
                conn.commit()
        return {"purged_memories": purged_memories}
