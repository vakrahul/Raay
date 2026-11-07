"""CSPRNG Human Approval Broker and Nonce Manager (Phases 57-58)."""
from datetime import datetime, timezone
import hashlib
import json
import secrets
import time
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class ApprovalRequest(BaseModel):
    nonce: str
    action_hash: str
    tool_name: str
    target_resource: str
    risk_tier: str
    reason: str
    session_id: str
    agent_id: str
    parameters: Dict[str, Any]
    created_at: float = Field(default_factory=time.time)
    ttl_seconds: float = 300.0  # 5 minute expiry


class ApprovalBroker:
    """Brokers human approvals with single-use cryptographically bound nonces."""

    def __init__(self):
        self._pending: Dict[str, ApprovalRequest] = {}

    def create_request(
        self,
        tool_name: str,
        target_resource: str,
        parameters: Dict[str, Any],
        risk_tier: str,
        reason: str,
        session_id: str,
        agent_id: str,
    ) -> ApprovalRequest:
        nonce = secrets.token_hex(32)
        # Compute deterministic action hash
        action_repr = json.dumps(
            {"tool": tool_name, "target": target_resource, "params": parameters},
            sort_keys=True,
        )
        action_hash = hashlib.sha256(action_repr.encode("utf-8")).hexdigest()

        req = ApprovalRequest(
            nonce=nonce,
            action_hash=action_hash,
            tool_name=tool_name,
            target_resource=target_resource,
            risk_tier=risk_tier,
            reason=reason,
            session_id=session_id,
            agent_id=agent_id,
            parameters=parameters,
        )
        self._pending[nonce] = req
        return req

    def verify_and_consume(self, nonce: str, expected_action_hash: Optional[str] = None) -> Optional[ApprovalRequest]:
        """Validate and single-use consume approval nonce. Rejects expired or mismatching nonces."""
        req = self._pending.get(nonce)
        if not req:
            return None

        # Check TTL
        if time.time() - req.created_at > req.ttl_seconds:
            self._pending.pop(nonce, None)
            return None

        # Check action hash binding
        if expected_action_hash and req.action_hash != expected_action_hash:
            return None

        return self._pending.pop(nonce)

    def describe_request(self, nonce: str) -> Optional[Dict[str, str]]:
        """Return display-safe fields for a pending request (no nonce/params)."""
        req = self._pending.get(nonce)
        if not req:
            return None
        return {
            "tool_name": req.tool_name,
            "target_resource": req.target_resource,
            "risk_tier": req.risk_tier,
            "reason": req.reason,
        }

    def pending_count(self, session_id: str | None = None) -> int:
        """Return the number of unexpired pending approvals, optionally per session."""
        now = time.time()
        if session_id is None:
            return sum(1 for r in self._pending.values() if now - r.created_at <= r.ttl_seconds)
        return sum(
            1
            for r in self._pending.values()
            if r.session_id == session_id and now - r.created_at <= r.ttl_seconds
        )

    def revoke_all_for_session(self, session_id: str) -> int:
        """Revoke and invalidate all pending approvals for a session."""
        to_remove = [k for k, v in self._pending.items() if v.session_id == session_id]
        for k in to_remove:
            del self._pending[k]
        return len(to_remove)
