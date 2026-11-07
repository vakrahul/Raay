"""Control-plane REST API: auth, admins, users, roles, settings, tasks, audit.

Every privileged action re-verifies permissions server-side on a fresh DB
read. Nothing is trusted from the client beyond the session token.
"""

import re
import secrets
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.platform.agent_config import ASSIGNABLE_KEYS, get_effective_agent_config
from src.platform.auth import (
    get_current_user,
    hash_password,
    hash_token,
    new_session_token,
    public_user,
    rate_limit,
    require_permission,
    verify_password,
)
from src.platform.control_db import TASK_STATUSES, USER_STATUSES, ControlPlaneDB

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,}$")


def _check_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=422, detail="Invalid email address")
    return email


def _check_password(password: str) -> str:
    if not isinstance(password, str) or not (10 <= len(password) <= 256):
        raise HTTPException(status_code=422, detail="Password must be 10-256 characters")
    return password


def _db(request: Request) -> ControlPlaneDB:
    db = getattr(request.app.state, "control_db", None)
    if db is None:
        raise HTTPException(status_code=500, detail="Control plane database not initialized")
    return db


def _actor(user: Optional[Dict[str, Any]]) -> Optional[int]:
    return user["id"] if user else None


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------
class BootstrapRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=256)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class CreateAdminRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: Optional[str] = Field(default=None, max_length=256)
    notes: str = Field(default="", max_length=500)


class UpdateAdminRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    status: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    password: Optional[str] = Field(default=None, max_length=256)


class PermissionGrantRequest(BaseModel):
    permission: str = Field(min_length=1, max_length=80)


class CreateUserRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=256)
    role: str = Field(default="USER", max_length=40)


class UpdateUserRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    status: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    add_roles: Optional[List[str]] = None
    remove_roles: Optional[List[str]] = None


class OwnConfigRequest(BaseModel):
    configuration: Dict[str, Any]


class SettingsPatchRequest(BaseModel):
    settings: Optional[Dict[str, Any]] = None
    flags: Optional[Dict[str, bool]] = None


class SecretPatchRequest(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=4096)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetConfirm(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    token: str = Field(min_length=10, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@router.post("/auth/bootstrap")
async def bootstrap(payload: BootstrapRequest, request: Request):
    db = _db(request)
    if db.count_users() > 0:
        raise HTTPException(status_code=403, detail="Bootstrap is only allowed on a fresh database")
    email = _check_email(payload.email)
    _check_password(payload.password)
    user = db.create_user(payload.name.strip(), email, hash_password(payload.password))
    db.assign_role(user["id"], "SUPER_ADMIN")
    db.create_admin_record(user["id"], None, notes="Initial Super Admin (bootstrap)")
    token = new_session_token()
    db.create_session(user["id"], hash_token(token))
    db.record_audit(user["id"], "BOOTSTRAP", f"Initial Super Admin created: {email}", {})
    return {"token": token, "user": public_user({**user, "roles": ["SUPER_ADMIN"],
                                                "permissions": db.get_user_permissions(user["id"])})}


@router.post("/auth/login", dependencies=[Depends(rate_limit())])
async def login(payload: LoginRequest, request: Request):
    db = _db(request)
    email = _check_email(payload.email)
    user = db.get_user_by_email(email)
    if not user or user["status"] != "active" or not verify_password(payload.password, user["password_hash"]):
        db.record_audit(
            (user or {}).get("id"), "LOGIN_FAILED", f"Failed login attempt for {email}", {}
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")
    db.update_user(user["id"], {"last_login": time.time()})
    token = new_session_token()
    db.create_session(user["id"], hash_token(token))
    db.record_audit(user["id"], "LOGIN", f"User logged in: {email}", {})
    fresh = db.get_user(user["id"]) or user
    return {"token": token, "user": public_user(
        {**fresh, "roles": db.get_user_roles(user["id"]),
         "permissions": db.get_user_permissions(user["id"])})}


@router.post("/auth/logout")
async def logout(request: Request, user: Dict[str, Any] = Depends(get_current_user)):
    db = _db(request)
    header = request.headers.get("authorization", "")
    token = header.partition(" ")[2].strip()
    if token:
        db.revoke_session(hash_token(token))
    db.record_audit(user["id"], "LOGOUT", f"User logged out: {user['email']}", {})
    return {"status": "logged_out"}


@router.get("/me")
async def me(user: Dict[str, Any] = Depends(get_current_user)):
    return public_user(user)


@router.patch("/me/config")
async def update_own_config(
    payload: OwnConfigRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("manage_own_settings")),
):
    import json

    db = _db(request)
    updated = db.update_user(user["id"], {"configuration": json.dumps(payload.configuration)})
    db.record_audit(user["id"], "SETTING_CHANGED", "Own configuration updated", {})
    return public_user({**(updated or user), "roles": user["roles"], "permissions": user["permissions"]})


@router.post("/auth/password-reset/request", dependencies=[Depends(rate_limit())])
async def password_reset_request(payload: PasswordResetRequest, request: Request):
    """Always returns success to avoid account enumeration.

    Email delivery is out of scope for this phase (no SMTP configured);
    the reset token must be delivered out-of-band. Tests read it from the DB.
    """
    db = _db(request)
    email = _check_email(payload.email)
    user = db.get_user_by_email(email)
    if user and user["status"] == "active":
        token = new_session_token()
        db.create_password_reset(user["id"], hash_token(token))
        db.record_audit(user["id"], "PASSWORD_RESET_REQUESTED", "Password reset requested", {})
    return {"status": "ok", "detail": "If the account exists, reset instructions were issued."}


@router.post("/auth/password-reset/confirm", dependencies=[Depends(rate_limit())])
async def password_reset_confirm(payload: PasswordResetConfirm, request: Request):
    db = _db(request)
    email = _check_email(payload.email)
    _check_password(payload.new_password)
    user = db.get_user_by_email(email)
    if not user or not db.consume_password_reset(user["id"], hash_token(payload.token)):
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    db.update_user(user["id"], {"password_hash": hash_password(payload.new_password)})
    db.revoke_user_sessions(user["id"])
    db.record_audit(user["id"], "PASSWORD_RESET", "Password reset completed", {})
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Roles & permissions (read)
# --------------------------------------------------------------------------
@router.get("/roles")
async def list_roles(request: Request, user: Dict[str, Any] = Depends(get_current_user)):
    return {"roles": _db(request).list_roles()}


@router.get("/permissions")
async def list_permissions(request: Request, user: Dict[str, Any] = Depends(get_current_user)):
    return {"permissions": _db(request).list_permissions()}


# --------------------------------------------------------------------------
# Admin management (SUPER_ADMIN via create_admin)
# --------------------------------------------------------------------------
def _require_admin_record(db: ControlPlaneDB, admin_id: int) -> Dict[str, Any]:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM admins WHERE id = ?", (admin_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Admin not found")
    return dict(row)


@router.get("/admins")
async def list_admins(request: Request, user: Dict[str, Any] = Depends(require_permission("create_admin"))):
    db = _db(request)
    out = []
    for admin in db.list_admins():
        roles = db.get_user_roles(admin["user_id"])
        out.append({**admin, "roles": roles,
                    "direct_permissions": db.get_user_direct_permissions(admin["user_id"])})
    return {"admins": out}


@router.post("/admins", status_code=201)
async def create_admin(
    payload: CreateAdminRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("create_admin")),
):
    db = _db(request)
    email = _check_email(payload.email)
    if payload.password is not None:
        _check_password(payload.password)
        password = payload.password
        generated = False
    else:
        password = secrets.token_urlsafe(16)
        generated = True
    if db.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="Email already registered")
    new_user = db.create_user(payload.name.strip(), email, hash_password(password))
    db.assign_role(new_user["id"], "ADMIN")
    admin = db.create_admin_record(new_user["id"], user["id"], notes=payload.notes)
    db.record_audit(user["id"], "ADMIN_CREATED",
                    f"Admin created: {email} (id {admin['id']})", {"admin_id": admin["id"]})
    result = {"admin": {**admin, "roles": ["ADMIN"]},
              "user": public_user({**new_user, "roles": ["ADMIN"], "permissions": []})}
    if generated:
        result["temporary_password"] = password
    return result


@router.patch("/admins/{admin_id}")
async def update_admin(
    admin_id: int,
    payload: UpdateAdminRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("create_admin")),
):
    db = _db(request)
    admin = _require_admin_record(db, admin_id)
    if payload.status is not None and payload.status not in ("active", "disabled"):
        raise HTTPException(status_code=422, detail="status must be active|disabled")
    if admin["user_id"] == user["id"] and payload.status == "disabled":
        raise HTTPException(status_code=403, detail="You cannot disable your own account")
    fields: Dict[str, Any] = {}
    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=422, detail="name must not be empty")
        fields["name"] = payload.name.strip()
    if payload.status is not None:
        fields["status"] = payload.status
    updated = db.update_user(admin["user_id"], fields)
    if payload.status == "disabled":
        db.revoke_user_sessions(admin["user_id"])
    db.record_audit(user["id"], "ADMIN_UPDATED",
                    f"Admin {admin_id} updated: {sorted(fields)}", {"admin_id": admin_id})
    return public_user({**(updated or {}), "roles": db.get_user_roles(admin["user_id"]),
                        "permissions": db.get_user_permissions(admin["user_id"])})


@router.delete("/admins/{admin_id}")
async def delete_admin(
    admin_id: int,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("delete_admin")),
):
    db = _db(request)
    admin = _require_admin_record(db, admin_id)
    if admin["user_id"] == user["id"]:
        raise HTTPException(status_code=403, detail="You cannot delete your own account")
    target_roles = db.get_user_roles(admin["user_id"])
    if "SUPER_ADMIN" in target_roles:
        remaining = [
            u for u in db.list_users()
            if u["id"] != admin["user_id"] and "SUPER_ADMIN" in db.get_user_roles(u["id"])
        ]
        if not remaining:
            raise HTTPException(status_code=403, detail="Cannot delete the last Super Admin")
    db.delete_user(admin["user_id"])
    db.record_audit(user["id"], "ADMIN_DELETED", f"Admin {admin_id} deleted", {"admin_id": admin_id})
    return {"status": "deleted"}


@router.patch("/admins/{admin_id}/password")
async def reset_admin_password(
    admin_id: int,
    payload: ResetPasswordRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("create_admin")),
):
    db = _db(request)
    admin = _require_admin_record(db, admin_id)
    if payload.password is not None:
        _check_password(payload.password)
        new_password = payload.password
        generated = False
    else:
        new_password = secrets.token_urlsafe(16)
        generated = True
    db.update_user(admin["user_id"], {"password_hash": hash_password(new_password)})
    db.revoke_user_sessions(admin["user_id"])
    db.record_audit(user["id"], "ADMIN_PASSWORD_RESET",
                    f"Password reset for admin {admin_id}", {"admin_id": admin_id})
    result: Dict[str, Any] = {"status": "ok"}
    if generated:
        result["temporary_password"] = new_password
    return result


@router.post("/admins/{admin_id}/permissions", status_code=201)
async def grant_admin_permission(
    admin_id: int,
    payload: PermissionGrantRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("manage_permissions")),
):
    db = _db(request)
    admin = _require_admin_record(db, admin_id)
    if not db.grant_user_permission(admin["user_id"], payload.permission, user["id"]):
        raise HTTPException(status_code=404, detail="Unknown permission")
    db.record_audit(user["id"], "PERMISSION_CHANGED",
                    f"Granted {payload.permission} to admin {admin_id}",
                    {"admin_id": admin_id, "permission": payload.permission, "granted": True})
    return {"status": "granted", "permissions": db.get_user_permissions(admin["user_id"])}


@router.delete("/admins/{admin_id}/permissions/{permission}")
async def revoke_admin_permission(
    admin_id: int,
    permission: str,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("manage_permissions")),
):
    db = _db(request)
    admin = _require_admin_record(db, admin_id)
    if not db.revoke_user_permission(admin["user_id"], permission):
        raise HTTPException(status_code=404, detail="Permission grant not found")
    db.record_audit(user["id"], "PERMISSION_CHANGED",
                    f"Revoked {permission} from admin {admin_id}",
                    {"admin_id": admin_id, "permission": permission, "granted": False})
    return {"status": "revoked", "permissions": db.get_user_permissions(admin["user_id"])}


# --------------------------------------------------------------------------
# User management
# --------------------------------------------------------------------------
@router.get("/users")
async def list_users(request: Request, user: Dict[str, Any] = Depends(require_permission("view_users"))):
    db = _db(request)
    out = []
    for u in db.list_users():
        out.append(public_user({**u, "roles": db.get_user_roles(u["id"]),
                               "permissions": db.get_user_permissions(u["id"])}))
    return {"users": out}


@router.post("/users", status_code=201)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("manage_users")),
):
    db = _db(request)
    email = _check_email(payload.email)
    _check_password(payload.password)
    if payload.role not in ("USER", "ADMIN"):
        raise HTTPException(status_code=422, detail="role must be USER|ADMIN")
    if db.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="Email already registered")
    new_user = db.create_user(payload.name.strip(), email, hash_password(payload.password))
    db.assign_role(new_user["id"], payload.role)
    if payload.role == "ADMIN":
        db.create_admin_record(new_user["id"], user["id"], notes="Created via user management")
    db.record_audit(user["id"], "USER_CREATED",
                    f"User created: {email} with role {payload.role}", {"user_id": new_user["id"]})
    return public_user({**new_user, "roles": db.get_user_roles(new_user["id"]),
                        "permissions": db.get_user_permissions(new_user["id"])})


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("manage_users")),
):
    db = _db(request)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.status is not None and payload.status not in ("active", "disabled"):
        raise HTTPException(status_code=422, detail="status must be active|disabled")
    if target["id"] == user["id"] and payload.status == "disabled":
        raise HTTPException(status_code=403, detail="You cannot disable your own account")
    fields: Dict[str, Any] = {}
    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=422, detail="name must not be empty")
        fields["name"] = payload.name.strip()
    if payload.status is not None:
        fields["status"] = payload.status
    if payload.configuration is not None:
        import json

        fields["configuration"] = json.dumps(payload.configuration)
    if fields:
        db.update_user(user_id, fields)
    # Role changes are permission changes: require manage_permissions.
    if payload.add_roles or payload.remove_roles:
        if "manage_permissions" not in (user.get("permissions") or []):
            raise HTTPException(status_code=403, detail="Missing required permission: manage_permissions")
        for role in payload.add_roles or []:
            if role not in ("USER", "ADMIN", "SUPER_ADMIN"):
                raise HTTPException(status_code=422, detail=f"Unknown role: {role}")
            if role == "SUPER_ADMIN" and "SUPER_ADMIN" not in (user.get("roles") or []):
                raise HTTPException(status_code=403, detail="Only a Super Admin can grant SUPER_ADMIN")
            db.assign_role(user_id, role)
        for role in payload.remove_roles or []:
            if role == "SUPER_ADMIN" and target["id"] == user["id"]:
                raise HTTPException(status_code=403, detail="You cannot remove your own SUPER_ADMIN role")
            db.remove_role(user_id, role)
    if payload.status == "disabled":
        db.revoke_user_sessions(user_id)
    db.record_audit(user["id"], "USER_DISABLED" if payload.status == "disabled" else "USER_UPDATED",
                    f"User {user_id} updated", {"user_id": user_id})
    fresh = db.get_user(user_id) or target
    return public_user({**fresh, "roles": db.get_user_roles(user_id),
                        "permissions": db.get_user_permissions(user_id)})


# --------------------------------------------------------------------------
# Settings & feature flags
# --------------------------------------------------------------------------
@router.get("/settings")
async def get_settings(request: Request, user: Dict[str, Any] = Depends(get_current_user)):
    db = _db(request)
    return {"settings": db.get_settings(include_secrets=False), "flags": db.get_flags()}


@router.patch("/settings")
async def patch_settings(
    payload: SettingsPatchRequest,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    from src.platform.agent_config import ASSIGNABLE_KEYS as _ASSIGNABLE

    db = _db(request)
    perms = set(user.get("permissions") or [])
    can_all = "manage_system_settings" in perms
    can_assigned = "manage_assigned_settings" in perms
    if not (can_all or can_assigned):
        raise HTTPException(status_code=403, detail="Missing required permission: manage_system_settings")
    changed = []
    for key, value in (payload.settings or {}).items():
        if key not in ControlPlaneDBSettingsKeys():
            raise HTTPException(status_code=422, detail=f"Unknown setting: {key}")
        if not can_all and key not in _ASSIGNABLE:
            raise HTTPException(status_code=403, detail=f"Setting requires manage_system_settings: {key}")
        if key == "allowed_applications" and (
            not isinstance(value, list) or not all(isinstance(v, str) for v in value)
        ):
            raise HTTPException(status_code=422, detail="allowed_applications must be a string list")
        db.set_setting(key, value, user["id"])
        changed.append(key)
    for key, enabled in (payload.flags or {}).items():
        if not can_all:
            raise HTTPException(status_code=403, detail="Feature flags require manage_system_settings")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=422, detail=f"Flag value must be boolean: {key}")
        if not db.set_flag(key, enabled, user["id"]):
            raise HTTPException(status_code=404, detail=f"Unknown feature flag: {key}")
        changed.append(f"flag:{key}")
    if changed:
        db.record_audit(user["id"], "SETTING_CHANGED", f"Settings updated: {sorted(changed)}",
                        {"keys": sorted(changed)})
    return {"settings": db.get_settings(include_secrets=False), "flags": db.get_flags()}


def ControlPlaneDBSettingsKeys():
    from src.platform.control_db import SEED_SETTINGS

    return set(SEED_SETTINGS)


@router.patch("/settings/secrets")
async def patch_secret(
    payload: SecretPatchRequest,
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("manage_system_settings")),
):
    db = _db(request)
    db.set_setting(payload.key, payload.value, user["id"], is_secret=True)
    db.record_audit(user["id"], "SETTING_CHANGED", f"Secret stored: {payload.key}",
                    {"key": payload.key, "secret": True})
    return {"status": "stored", "key": payload.key}


# --------------------------------------------------------------------------
# Tasks & audit
# --------------------------------------------------------------------------
def _can_see_all_tasks(user: Dict[str, Any]) -> bool:
    return "view_usage" in (user.get("permissions") or [])


@router.get("/tasks")
async def list_tasks(
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
    status: Optional[str] = None,
    limit: int = 100,
    mine: bool = False,
):
    db = _db(request)
    scope_uid = user["id"] if (mine or not _can_see_all_tasks(user)) else None
    return {"tasks": db.list_tasks(user_id=scope_uid, status=status, limit=limit)}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int,
    request: Request,
    user: Dict[str, Any] = Depends(get_current_user),
):
    db = _db(request)
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not _can_see_all_tasks(user) and task["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/audit")
async def list_audit(
    request: Request,
    user: Dict[str, Any] = Depends(require_permission("view_system_logs")),
    event_type: Optional[str] = None,
    limit: int = 100,
):
    return {"audit": _db(request).list_audit(event_type=event_type, limit=limit)}


@router.get("/audit/verify")
async def verify_audit(
    request: Request, user: Dict[str, Any] = Depends(require_permission("view_system_logs"))
):
    return _db(request).verify_audit_chain()


# --------------------------------------------------------------------------
# Agent configuration surface
# --------------------------------------------------------------------------
@router.get("/api/v1/agent/config")
async def agent_config(request: Request, user: Dict[str, Any] = Depends(require_permission("use_agent"))):
    return get_effective_agent_config(_db(request))
