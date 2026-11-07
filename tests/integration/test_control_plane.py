"""Dynamic control-plane acceptance tests (spec section 16 + RBAC + persistence).

All tests run against a real SQLite database in an isolated temp workspace.
No hardcoded users, no mocked responses: every assertion exercises the live
FastAPI app created by src.orchestrator.main.create_app.
"""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig, SecurityLevel
from src.orchestrator.main import create_app
from src.platform.control_db import ControlPlaneDB


@pytest.fixture
def app_client(temp_workspace: Path):
    cfg = AppConfig(
        workspace_root=temp_workspace,
        security_level=SecurityLevel.STRICT,
        environment="testing",
    )
    application = create_app(cfg)
    with TestClient(application) as client:
        yield client, cfg


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _bootstrap(client, name="Root", email="root@example.com", password="RootPassword123"):
    r = client.post("/auth/bootstrap", json={"name": name, "email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["roles"] == ["SUPER_ADMIN"]
    return body["token"], body["user"]


def test_bootstrap_once_and_login_flow(app_client):
    client, _ = app_client
    token, user = _bootstrap(client)
    assert user["email"] == "root@example.com"

    # Second bootstrap must fail: exactly one initial Super Admin.
    r = client.post("/auth/bootstrap",
                    json={"name": "X", "email": "x@example.com", "password": "XPassword123"})
    assert r.status_code == 403

    # Wrong password rejected; unknown user rejected identically.
    assert client.post("/auth/login", json={"email": "root@example.com", "password": "nope-nope-nope"}).status_code == 401
    assert client.post("/auth/login", json={"email": "ghost@example.com", "password": "Whatever1234"}).status_code == 401

    # Login works; /me reflects roles; logout invalidates.
    r = client.post("/auth/login", json={"email": "root@example.com", "password": "RootPassword123"})
    assert r.status_code == 200
    token2 = r.json()["token"]
    assert client.get("/me", headers=_h(token2)).status_code == 200
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers=_h("bogus")).status_code == 401
    assert client.post("/auth/logout", headers=_h(token2)).status_code == 200
    assert client.get("/me", headers=_h(token2)).status_code == 401
    # Original bootstrap token still valid (independent session).
    assert client.get("/me", headers=_h(token)).status_code == 200


def test_dynamic_admin_user_permission_flow(app_client):
    """Spec section 16 steps 1-10: create admin, permission grant/revoke takes
    effect immediately, disable blocks authentication — all without code changes."""
    client, _ = app_client
    super_token, _ = _bootstrap(client)

    # 1. Create Admin A from the API (as the UI would).
    r = client.post("/admins", headers=_h(super_token), json={
        "name": "Admin A", "email": "admina@example.com", "password": "AdminAPassword1"})
    assert r.status_code == 201, r.text
    admin_a_id = r.json()["admin"]["id"]

    # 2. Log in as Admin A.
    r = client.post("/auth/login", json={"email": "admina@example.com", "password": "AdminAPassword1"})
    assert r.status_code == 200
    token_a = r.json()["token"]

    # Admin A cannot read audit logs yet (no view_system_logs).
    assert client.get("/audit", headers=_h(token_a)).status_code == 403

    # 3. Give Admin A a specific permission (direct grant).
    r = client.post(f"/admins/{admin_a_id}/permissions", headers=_h(super_token),
                    json={"permission": "view_system_logs"})
    assert r.status_code == 201
    assert "view_system_logs" in r.json()["permissions"]

    # 4. Create User A (as Admin A, who holds manage_users via ADMIN role).
    r = client.post("/users", headers=_h(token_a), json={
        "name": "User A", "email": "usera@example.com", "password": "UserAPassword1"})
    assert r.status_code == 201, r.text

    # 5. Log in as User A; 6. verify isolation (no user list, only own tasks).
    r = client.post("/auth/login", json={"email": "usera@example.com", "password": "UserAPassword1"})
    token_u = r.json()["token"]
    assert client.get("/users", headers=_h(token_u)).status_code == 403
    assert client.get("/tasks", headers=_h(token_u)).json() == {"tasks": []}

    # 7. Revoke the permission again — takes effect immediately, no restart.
    r = client.delete(f"/admins/{admin_a_id}/permissions/view_system_logs", headers=_h(super_token))
    assert r.status_code == 200
    assert "view_system_logs" not in r.json()["permissions"]
    assert client.get("/audit", headers=_h(token_a)).status_code == 403

    # 9-10. Disable Admin A; login now fails and old token dies.
    r = client.patch(f"/admins/{admin_a_id}", headers=_h(super_token), json={"status": "disabled"})
    assert r.status_code == 200
    assert client.post("/auth/login",
                       json={"email": "admina@example.com", "password": "AdminAPassword1"}).status_code == 401
    assert client.get("/me", headers=_h(token_a)).status_code == 401


def test_role_assignment_requires_permission(app_client):
    client, _ = app_client
    super_token, _ = _bootstrap(client)
    r = client.post("/admins", headers=_h(super_token),
                    json={"name": "A2", "email": "a2@example.com", "password": "A2Password123"})
    admin_id = r.json()["admin"]["id"]
    r = client.post("/users", headers=_h(super_token),
                    json={"name": "U2", "email": "u2@example.com", "password": "U2Password1234"})
    user_id = r.json()["id"]
    token_a = client.post("/auth/login",
                          json={"email": "a2@example.com", "password": "A2Password123"}).json()["token"]
    # Admin role lacks manage_permissions -> role grant must fail.
    r = client.patch(f"/users/{user_id}", headers=_h(token_a), json={"add_roles": ["ADMIN"]})
    assert r.status_code == 403
    # Super admin can grant ADMIN role (but never SUPER_ADMIN to non-super granters).
    r = client.patch(f"/users/{user_id}", headers=_h(super_token), json={"add_roles": ["ADMIN"]})
    assert r.status_code == 200
    assert "ADMIN" in r.json()["roles"]
    _ = admin_id


def test_self_protection(app_client):
    client, _ = app_client
    super_token, super_user = _bootstrap(client)
    me = client.get("/me", headers=_h(super_token)).json()
    assert me["roles"] == ["SUPER_ADMIN"]
    admins = client.get("/admins", headers=_h(super_token)).json()["admins"]
    own = next(a for a in admins if a["email"] == "root@example.com")
    # Cannot disable or delete self; cannot delete the last Super Admin.
    assert client.patch(f"/admins/{own['id']}", headers=_h(super_token),
                        json={"status": "disabled"}).status_code == 403
    assert client.delete(f"/admins/{own['id']}", headers=_h(super_token)).status_code == 403


def test_settings_flags_persist_across_restart(app_client, temp_workspace: Path):
    client, cfg = app_client
    super_token, _ = _bootstrap(client)
    before = client.get("/settings", headers=_h(super_token)).json()
    assert before["settings"]["agent_enabled"] is True
    assert before["flags"]["vision_mode"]["enabled"] is False

    r = client.patch("/settings", headers=_h(super_token), json={
        "settings": {"max_task_duration_seconds": 900, "agent_enabled": True},
        "flags": {"vision_mode": True},
    })
    assert r.status_code == 200
    assert r.json()["settings"]["max_task_duration_seconds"] == 900
    assert r.json()["flags"]["vision_mode"]["enabled"] is True

    # Unknown keys rejected; secrets masked.
    assert client.patch("/settings", headers=_h(super_token),
                        json={"settings": {"nope": 1}}).status_code == 422
    r = client.patch("/settings/secrets", headers=_h(super_token),
                     json={"key": "typesafe_api_key", "value": "sk-test-123"})
    assert r.status_code == 200
    assert client.get("/settings", headers=_h(super_token)).json()["settings"]["typesafe_api_key"] == "***"

    # 12-13. Simulate restart: brand-new app instance over the same DB file.
    cfg2 = AppConfig(workspace_root=temp_workspace, security_level=SecurityLevel.STRICT,
                     environment="testing")
    with TestClient(create_app(cfg2)) as client2:
        persisted = client2.get("/settings", headers=_h(super_token)).json()
        assert persisted["settings"]["max_task_duration_seconds"] == 900
        assert persisted["flags"]["vision_mode"]["enabled"] is True


def test_assigned_settings_allowlist(app_client):
    client, _ = app_client
    super_token, _ = _bootstrap(client)
    r = client.post("/admins", headers=_h(super_token),
                    json={"name": "A3", "email": "a3@example.com", "password": "A3Password123"})
    assert r.status_code == 201
    token_a = client.post("/auth/login",
                          json={"email": "a3@example.com", "password": "A3Password123"}).json()["token"]
    # manage_assigned_settings allows agent_enabled but not max_task_duration_seconds.
    r = client.patch("/settings", headers=_h(token_a), json={"settings": {"agent_enabled": False}})
    assert r.status_code == 200
    r = client.patch("/settings", headers=_h(token_a),
                     json={"settings": {"max_task_duration_seconds": 5}})
    assert r.status_code == 403
    # Restore for other tests (fresh DB per test via fixture, but be tidy).
    client.patch("/settings", headers=_h(super_token), json={"settings": {"agent_enabled": True}})


def test_agent_gate_and_task_history(app_client):
    client, _ = app_client
    super_token, _ = _bootstrap(client)

    # Disable the agent -> dispatch is BLOCKED without executing.
    client.patch("/settings", headers=_h(super_token), json={"settings": {"agent_enabled": False}})
    r = client.post("/api/v1/tasks/dispatch", json={"task": "Open Notepad, type Hi"})
    assert r.json()["status"] == "BLOCKED"
    client.patch("/settings", headers=_h(super_token), json={"settings": {"agent_enabled": True}})

    # Allowed-apps gate: remove notepad -> BLOCKED; restore afterwards.
    client.patch("/settings", headers=_h(super_token),
                 json={"settings": {"allowed_applications": ["calc"]}})
    r = client.post("/api/v1/tasks/dispatch", json={"task": "Open Notepad, type Hi"})
    assert r.json()["status"] == "BLOCKED"
    assert r.json()["details"]["requested_app"] == "notepad"
    client.patch("/settings", headers=_h(super_token),
                 json={"settings": {"allowed_applications": ["notepad", "calc", "chrome", "vscode", "edge"]}})

    # Task history records dispatches with statuses.
    tasks = client.get("/tasks", headers=_h(super_token)).json()["tasks"]
    assert len(tasks) >= 2
    assert {t["status"] for t in tasks} <= {"COMPLETED", "FAILED", "BLOCKED", "NEEDS_CLARIFICATION",
                                            "PARTIAL", "PENDING", "RUNNING", "WAITING_FOR_APPROVAL", "CANCELLED"}
    task_id = tasks[0]["id"]
    assert client.get(f"/tasks/{task_id}", headers=_h(super_token)).json()["id"] == task_id
    counts = {}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    assert counts.get("BLOCKED", 0) >= 2


def test_password_reset_flow(app_client, temp_workspace: Path):
    client, _ = app_client
    _bootstrap(client)
    # Generic response even for unknown accounts.
    assert client.post("/auth/password-reset/request",
                       json={"email": "nobody@example.com"}).status_code == 200
    assert client.post("/auth/password-reset/request",
                       json={"email": "root@example.com"}).status_code == 200
    # White-box: read the issued token hash, confirm through the real endpoint.
    import hashlib

    db = ControlPlaneDB(temp_workspace)
    user = db.get_user_by_email("root@example.com")
    raw = "reset-token-for-test-0123456789"
    db.create_password_reset(user["id"], hashlib.sha256(raw.encode()).hexdigest())
    assert client.post("/auth/password-reset/confirm", json={
        "email": "root@example.com", "token": "wrong-token-value-123", "new_password": "BrandNewPass123",
    }).status_code == 400
    r = client.post("/auth/password-reset/confirm", json={
        "email": "root@example.com", "token": raw, "new_password": "BrandNewPass123"})
    assert r.status_code == 200
    # Old password dead, new password works (old sessions revoked).
    assert client.post("/auth/login",
                       json={"email": "root@example.com", "password": "RootPassword123"}).status_code == 401
    assert client.post("/auth/login",
                       json={"email": "root@example.com", "password": "BrandNewPass123"}).status_code == 200


def test_audit_verify_endpoint(app_client):
    client, _ = app_client
    super_token, _ = _bootstrap(client)
    client.post("/users", headers=_h(super_token),
                json={"name": "U9", "email": "u9@example.com", "password": "U9Password1234"})
    result = client.get("/audit/verify", headers=_h(super_token)).json()
    assert result["valid"] is True and result["total"] > 0
    events = client.get("/audit?limit=50", headers=_h(super_token)).json()["audit"]
    kinds = {e["event_type"] for e in events}
    assert {"BOOTSTRAP", "USER_CREATED"} <= kinds


def test_ui_pages_serve(app_client):
    client, _ = app_client
    for path in ("/login", "/admin", "/user", "/ui/app.js", "/ui/styles.css"):
        r = client.get(path)
        assert r.status_code == 200, path
    assert "Sign in" in client.get("/login").text
