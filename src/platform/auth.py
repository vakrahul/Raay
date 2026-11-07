"""Platform authentication: PBKDF2 password hashing, opaque sessions, RBAC, rate limits.

No new dependencies: hashlib/secrets stdlib. Sessions are opaque random
tokens (sha256 stored); transport is Authorization: Bearer on loopback,
so there is no cookie/CSRF surface.
"""

import hashlib
import hmac
import secrets
import time
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Request, status

from src.platform.control_db import ControlPlaneDB

HASH_ALGORITHM = "pbkdf2-sha256"
HASH_ITERATIONS = 600_000
SESSION_TTL_SECONDS = 43200  # 12h, sliding


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters.")
    if len(password) > 256:
        raise ValueError("Password must be at most 256 characters.")
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ITERATIONS)
    return (
        f"{HASH_ALGORITHM}${HASH_ITERATIONS}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != HASH_ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except Exception:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_db(request: Request) -> ControlPlaneDB:
    db = getattr(request.app.state, "control_db", None)
    if db is None:
        raise HTTPException(status_code=500, detail="Control plane database not initialized")
    return db


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def get_current_user(request: Request) -> Dict[str, Any]:
    """Authenticate via session token;Touch sliding expiry; enforce active status."""
    db = get_db(request)
    token = _bearer_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")
    session = db.get_session(hash_token(token))
    now = time.time()
    if not session or session["revoked"] or session["expires_at"] <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
    user = db.get_user(session["user_id"])
    if not user or user["status"] != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account disabled")
    db.touch_session(hash_token(token))
    roles = db.get_user_roles(user["id"])
    permissions = db.get_user_permissions(user["id"])
    return {**user, "roles": roles, "permissions": permissions}


def require_permission(permission: str):
    """Dependency factory: 403 unless the authenticated user holds `permission` (fresh DB read)."""

    async def checker(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        if permission not in (user.get("permissions") or []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {permission}",
            )
        return user

    return checker


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """Safe user representation (never includes password_hash)."""
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "status": user["status"],
        "roles": user.get("roles", []),
        "permissions": user.get("permissions", []),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        "last_login": user.get("last_login"),
        "configuration": _safe_json(user.get("configuration") or "{}"),
        "usage_metadata": _safe_json(user.get("usage_metadata") or "{}"),
    }


def _safe_json(raw: str) -> Any:
    import json

    try:
        return json.loads(raw)
    except Exception:
        return {}


# -- rate limiting (in-memory, per instance; auth endpoints only) ------------
_rate_buckets: Dict[str, List[float]] = {}


def rate_limit(max_per_minute: int = 30):
    async def checker(request: Request) -> None:
        try:
            limit = int(request.app.state.control_db.get_settings().get(
                "rate_limit_auth_per_minute", max_per_minute
            ) or max_per_minute)
        except Exception:
            limit = max_per_minute
        client = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = _rate_buckets.setdefault(client, [])
        cutoff = now - 60.0
        bucket[:] = [t for t in bucket if t > cutoff]
        if len(bucket) >= max(1, limit):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many authentication attempts; slow down.",
            )
        bucket.append(now)

    return checker
