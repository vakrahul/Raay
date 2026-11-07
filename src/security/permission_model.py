"""Agent identity and granular least-privilege permission model (Phases 52-54, 58)."""
from datetime import datetime, timezone
from enum import Enum
import fnmatch
from pathlib import Path
import secrets
from typing import Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


class PermissionScope(str, Enum):
    FS_READ = "fs:read"
    FS_WRITE = "fs:write"
    FS_DELETE = "fs:delete"
    PROC_EXEC = "proc:exec"
    NET_EGRESS = "net:egress"
    UIA_CONTROL = "uia:control"


class AgentIdentity(BaseModel):
    """Unique identity assigned to an agent execution session."""
    agent_id: str
    session_id: str
    role: str
    is_active: bool = True
    is_tainted: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PermissionGrant(BaseModel):
    """Granular permission grant bound to a resource pattern."""
    scope: PermissionScope
    resource_pattern: str  # e.g., "D:/Interveiewsass/src/*" or "git.exe"
    requires_approval: bool = True


class PermissionManager:
    """Independent host-side permission authority."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self._identities: Dict[str, AgentIdentity] = {}
        # agent_id -> list of grants
        self._grants: Dict[str, List[PermissionGrant]] = {}
        self._revoked_agents: Set[str] = set()

    def create_agent_identity(self, role: str, session_id: Optional[str] = None) -> AgentIdentity:
        agent_id = f"agt_{secrets.token_hex(8)}"
        sess_id = session_id or f"sess_{secrets.token_hex(8)}"
        identity = AgentIdentity(agent_id=agent_id, session_id=sess_id, role=role)
        self._identities[agent_id] = identity

        # Default least-privilege grants
        default_grants = [
            PermissionGrant(
                scope=PermissionScope.FS_READ,
                resource_pattern=f"{self.workspace_root}/*",
                requires_approval=False,
            )
        ]
        if role == "coder":
            default_grants.append(
                PermissionGrant(
                    scope=PermissionScope.FS_WRITE,
                    resource_pattern=f"{self.workspace_root}/*",
                    requires_approval=True,
                )
            )
        self._grants[agent_id] = default_grants
        return identity

    def check_permission(
        self,
        agent_id: str,
        scope: PermissionScope,
        target_resource: str,
    ) -> Tuple[bool, bool]:
        """Returns (is_granted, requires_approval). If revoked or not granted, returns (False, False)."""
        if agent_id in self._revoked_agents:
            return False, False

        identity = self._identities.get(agent_id)
        if not identity or not identity.is_active:
            return False, False

        grants = self._grants.get(agent_id, [])
        for grant in grants:
            if grant.scope == scope:
                # Check resource pattern match
                normalized_target = target_resource.replace("\\", "/")
                normalized_pattern = grant.resource_pattern.replace("\\", "/")
                if fnmatch.fnmatch(normalized_target, normalized_pattern):
                    # If agent is tainted, force human approval
                    req_appr = grant.requires_approval or identity.is_tainted
                    return True, req_appr

        return False, False

    def revoke_agent(self, agent_id: str) -> None:
        """Revoke all permissions for an agent immediately."""
        self._revoked_agents.add(agent_id)
        if agent_id in self._identities:
            self._identities[agent_id].is_active = False

    def taint_agent(self, agent_id: str) -> None:
        """Mark agent as tainted by untrusted input, forcing approval requirements."""
        if agent_id in self._identities:
            self._identities[agent_id].is_tainted = True
