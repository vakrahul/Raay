"""Structured logging and tamper-evident audit log infrastructure.

Enforces:
1. ISO-8601 UTC timestamps.
2. Contextual correlation (session_id, agent_id, operation_id).
3. Secret and token redaction filter.
4. Cryptographic SHA-256 hash chaining to ensure audit integrity.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Regex patterns matching API keys, tokens, and credentials
SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"vach_[a-zA-Z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{12,}", re.IGNORECASE),
    re.compile(
        r"(x-api-key(?:-id)?|api[-_]key)\s*[:=]\s*['\"]?[^\s,'\"\r\n;]+['\"]?",
        re.IGNORECASE,
    ),
    re.compile(r"password['\"]?\s*[:=]\s*['\"][^'\"]+['\"]", re.IGNORECASE),
    re.compile(r"BEGIN (RSA|EC|OPENSSH) PRIVATE KEY", re.IGNORECASE),
]


def redact_secrets(data: Any) -> Any:
    """Recursively scrub secrets from strings, dictionaries, and lists."""
    if isinstance(data, str):
        cleaned = data
        for pattern in SECRET_PATTERNS:
            cleaned = pattern.sub("[REDACTED_SECRET]", cleaned)
        return cleaned
    elif isinstance(data, dict):
        cleaned_dict = {}
        for k, v in data.items():
            # If key name itself indicates sensitive data, redact value entirely
            if any(
                term in str(k).lower()
                for term in [
                    "api_key",
                    "api-key",
                    "x-api-key",
                    "authorization",
                    "proxy-authorization",
                    "secret",
                    "password",
                    "auth_token",
                    "access_token",
                    "refresh_token",
                    "session_token",
                    "private_key",
                ]
            ):
                cleaned_dict[k] = "[REDACTED_SECRET]"
            else:
                cleaned_dict[k] = redact_secrets(v)
        return cleaned_dict
    elif isinstance(data, tuple):
        return tuple(redact_secrets(item) for item in data)
    elif isinstance(data, list):
        return [redact_secrets(item) for item in data]
    return data


class AuditEvent:
    """Structured security audit event."""
    def __init__(
        self,
        event_type: str,
        message: str,
        level: str = "INFO",
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        operation_id: Optional[str] = None,
        prev_hash: str = "0" * 64,
    ):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.event_type = event_type
        self.message = redact_secrets(message)
        self.level = level
        self.payload = redact_secrets(payload or {})
        self.session_id = session_id or "system"
        self.agent_id = agent_id or "system"
        self.operation_id = operation_id or "op_0"
        self.prev_hash = prev_hash
        self.hash = self._calculate_hash()

    def _calculate_hash(self) -> str:
        """Compute SHA-256 hash over canonical representation including previous hash."""
        content = {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "level": self.level,
            "message": self.message,
            "payload": self.payload,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "operation_id": self.operation_id,
            "prev_hash": self.prev_hash,
        }
        canonical_json = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "level": self.level,
            "message": self.message,
            "payload": self.payload,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "operation_id": self.operation_id,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


class AuditLogger:
    """Tamper-evident audit logger appending to hash-chained JSONL files."""
    def __init__(self, log_file: Path):
        self.log_file = log_file.resolve()
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.last_hash = self._read_last_hash()

    def _read_last_hash(self) -> str:
        """Read the last hash from existing log file or return initial seed."""
        if not self.log_file.exists() or self.log_file.stat().st_size == 0:
            return "0" * 64
        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                return "0" * 64
            last_line = lines[-1].strip()
            try:
                record = json.loads(last_line)
                return record.get("hash", "0" * 64)
            except json.JSONDecodeError:
                return "0" * 64

    def log(
        self,
        event_type: str,
        message: str,
        level: str = "INFO",
        payload: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        operation_id: Optional[str] = None,
    ) -> AuditEvent:
        """Emit an audit event and append to the hash-chained audit log."""
        # Re-read the chain tip on every write so multiple logger instances
        # in one process (dispatcher engine + per-request harnesses) extend a
        # single linear chain instead of forking from a stale cached tip.
        self.last_hash = self._read_last_hash()
        event = AuditEvent(
            event_type=event_type,
            message=message,
            level=level,
            payload=payload,
            session_id=session_id,
            agent_id=agent_id,
            operation_id=operation_id,
            prev_hash=self.last_hash,
        )
        self.last_hash = event.hash
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict()) + "\n")
        return event

    def event_count(self) -> int:
        """Return the number of non-empty records in the log file."""
        if not self.log_file.exists():
            return 0
        with open(self.log_file, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def verify_integrity(self) -> bool:
        """Verify that every entry in the log chain has a valid SHA-256 predecessor hash."""
        if not self.log_file.exists():
            return True
        expected_prev_hash = "0" * 64
        with open(self.log_file, "r", encoding="utf-8") as f:
            for _line_no, line in enumerate(f, start=1):
                clean_line = line.strip()
                if not clean_line:
                    continue
                try:
                    record = json.loads(clean_line)
                except json.JSONDecodeError:
                    return False
                if record.get("prev_hash") != expected_prev_hash:
                    return False
                # Recompute hash
                content = {
                    "timestamp": record["timestamp"],
                    "event_type": record["event_type"],
                    "level": record["level"],
                    "message": record["message"],
                    "payload": record["payload"],
                    "session_id": record["session_id"],
                    "agent_id": record["agent_id"],
                    "operation_id": record["operation_id"],
                    "prev_hash": record["prev_hash"],
                }
                canonical_json = json.dumps(content, sort_keys=True, separators=(",", ":"))
                expected_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
                if record.get("hash") != expected_hash:
                    return False
                expected_prev_hash = record["hash"]
        return True
