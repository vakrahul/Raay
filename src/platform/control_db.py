"""Control-plane database: relational schema, migrations, seed data, repository.

SQLite + WAL (same pattern as TaskStateEngine). One database file per
workspace at <workspace>/.winai/control_plane.db, so tests get isolated
databases and a fresh checkout works with zero manual setup.
"""

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_FILENAME = "control_plane.db"
CURRENT_SCHEMA_VERSION = 2

# --------------------------------------------------------------------------
# Seed data (only default roles/permissions/settings/flags — never users)
# --------------------------------------------------------------------------

SEED_PERMISSIONS = [
    ("create_admin", "Create new administrator accounts"),
    ("delete_admin", "Remove administrator accounts"),
    ("manage_users", "Create, update and disable users"),
    ("view_users", "List and inspect users"),
    ("view_usage", "Inspect aggregate usage and all task history"),
    ("manage_assigned_settings", "Update assigned subset of system settings"),
    ("manage_system_settings", "Update any system setting or secret"),
    ("manage_permissions", "Grant or revoke permissions"),
    ("view_system_logs", "Read system audit logs"),
    ("use_agent", "Execute tasks through the Windows agent"),
    ("view_own_tasks", "View own task history"),
    ("view_own_history", "View own activity history"),
    ("manage_own_settings", "Update own configuration"),
]

SEED_ROLES = {
    "SUPER_ADMIN": (
        "Full system control",
        ["create_admin", "delete_admin", "manage_users", "view_users",
         "view_usage", "manage_assigned_settings", "manage_system_settings",
         "manage_permissions", "view_system_logs", "use_agent",
         "view_own_tasks", "view_own_history", "manage_own_settings"],
    ),
    "ADMIN": (
        "Delegated administration",
        ["manage_users", "view_users", "view_usage", "manage_assigned_settings",
         "use_agent", "view_own_tasks", "view_own_history", "manage_own_settings"],
    ),
    "USER": (
        "Standard agent user",
        ["use_agent", "view_own_tasks", "view_own_history", "manage_own_settings"],
    ),
}

SEED_SETTINGS = {
    "agent_enabled": True,
    "default_provider": "mock",
    "default_model": "mock-gpt-4o",
    "max_task_duration_seconds": 600,
    "approval_policy": "strict",
    "log_level": "INFO",
    "rate_limit_auth_per_minute": 30,
    "allowed_applications": ["notepad", "calc", "chrome", "vscode", "edge"],
}

SEED_FLAGS = {
    "browser_control": (True, "Allow browser tab automation"),
    "filesystem_control": (True, "Allow workspace filesystem operations"),
    "vision_mode": (False, "Allow screenshot-based vision fallback"),
    "experimental_jev": (False, "Enable experimental JEV decision advisories"),
}

TASK_STATUSES = {
    "PENDING", "RUNNING", "WAITING_FOR_APPROVAL", "COMPLETED",
    "FAILED", "CANCELLED", "BLOCKED", "NEEDS_CLARIFICATION",
}

USER_STATUSES = {"active", "disabled"}


def _utcnow() -> float:
    return time.time()


class ControlPlaneDB:
    """Repository over the control-plane SQLite database."""

    def __init__(self, workspace_root: Path):
        self.db_path = (workspace_root.resolve() / ".winai" / DB_FILENAME)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._run_migrations()

    # -- low-level ---------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _run_migrations(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
            )
            applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            if 1 not in applied:
                self._migrate_v1(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                    (_utcnow(),),
                )
            if 2 not in applied:
                self._migrate_v2_seed(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (2, ?)",
                    (_utcnow(),),
                )

    @staticmethod
    def _migrate_v1(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_login REAL,
                configuration TEXT NOT NULL DEFAULT '{}',
                usage_metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX idx_users_email ON users(email);
            CREATE INDEX idx_users_status ON users(status);

            CREATE TABLE roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE role_permissions (
                role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
                PRIMARY KEY (role_id, permission_id)
            );

            CREATE TABLE user_roles (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, role_id)
            );

            CREATE TABLE user_permissions (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
                granted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                granted_at REAL NOT NULL,
                PRIMARY KEY (user_id, permission_id)
            );

            CREATE TABLE admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at REAL NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                is_secret INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE feature_flags (
                key TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                description TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,
                updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                result_summary TEXT,
                failure_reason TEXT,
                approval_status TEXT,
                audit_reference INTEGER
            );
            CREATE INDEX idx_tasks_user_status ON tasks(user_id, status, created_at);

            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            );
            CREATE INDEX idx_audit_event_time ON audit_logs(event_type, timestamp);
            CREATE INDEX idx_audit_actor ON audit_logs(actor_user_id);

            CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                last_seen REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX idx_sessions_user ON sessions(user_id, expires_at);

            CREATE TABLE password_resets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX idx_resets_token ON password_resets(token_hash);
            """
        )

    @staticmethod
    def _migrate_v2_seed(conn: sqlite3.Connection) -> None:
        now = _utcnow()
        perm_ids: Dict[str, int] = {}
        for name, desc in SEED_PERMISSIONS:
            cur = conn.execute(
                "INSERT OR IGNORE INTO permissions (name, description) VALUES (?, ?)",
                (name, desc),
            )
            row = conn.execute(
                "SELECT id FROM permissions WHERE name = ?", (name,)
            ).fetchone()
            perm_ids[name] = row["id"] if row else cur.lastrowid
        for role_name, (desc, perms) in SEED_ROLES.items():
            conn.execute(
                "INSERT OR IGNORE INTO roles (name, description) VALUES (?, ?)",
                (role_name, desc),
            )
            role_id = conn.execute(
                "SELECT id FROM roles WHERE name = ?", (role_name,)
            ).fetchone()["id"]
            for perm in perms:
                conn.execute(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                    (role_id, perm_ids[perm]),
                )
        for key, value in SEED_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, is_secret, updated_at, updated_by)"
                " VALUES (?, ?, 0, ?, NULL)",
                (key, json.dumps(value), now),
            )
        for key, (enabled, desc) in SEED_FLAGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO feature_flags (key, enabled, description, updated_at, updated_by)"
                " VALUES (?, ?, ?, ?, NULL)",
                (key, 1 if enabled else 0, desc, now),
            )

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row is not None else None

    # -- users -------------------------------------------------------------
    def count_users(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, name: str, email: str, password_hash: str,
                    status: str = "active") -> Dict[str, Any]:
        now = _utcnow()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (name, email, password_hash, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (name, email.lower(), password_hash, status, now, now),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
            return dict(row) if row else {}

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            return self._row_to_dict(
                conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            )

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            return self._row_to_dict(
                conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
            )

    def update_user(self, user_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        allowed = {"name", "status", "configuration", "usage_metadata", "password_hash", "last_login"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get_user(user_id)
        sets["updated_at"] = _utcnow()
        with self._connect() as conn:
            conn.execute(
                f"UPDATE users SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                (*sets.values(), user_id),
            )
            return self.get_user(user_id)

    def list_users(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id")]

    def delete_user(self, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cur.rowcount > 0

    def get_user_roles(self, user_id: int) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r.name FROM roles r JOIN user_roles ur ON ur.role_id = r.id"
                " WHERE ur.user_id = ? ORDER BY r.name",
                (user_id,),
            ).fetchall()
            return [r[0] for r in rows]

    def get_user_permissions(self, user_id: int) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT p.name FROM permissions p"
                " JOIN role_permissions rp ON rp.permission_id = p.id"
                " JOIN user_roles ur ON ur.role_id = rp.role_id"
                " WHERE ur.user_id = ?"
                " UNION "
                "SELECT p.name FROM permissions p"
                " JOIN user_permissions up ON up.permission_id = p.id"
                " WHERE up.user_id = ? ORDER BY 1",
                (user_id, user_id),
            ).fetchall()
            return [r[0] for r in rows]

    def grant_user_permission(self, user_id: int, perm_name: str,
                              granted_by: Optional[int]) -> bool:
        with self._connect() as conn:
            perm = conn.execute(
                "SELECT id FROM permissions WHERE name = ?", (perm_name,)
            ).fetchone()
            if not perm:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO user_permissions"
                " (user_id, permission_id, granted_by, granted_at)"
                " VALUES (?, ?, ?, ?)",
                (user_id, perm["id"], granted_by, _utcnow()),
            )
            return True

    def revoke_user_permission(self, user_id: int, perm_name: str) -> bool:
        with self._connect() as conn:
            perm = conn.execute(
                "SELECT id FROM permissions WHERE name = ?", (perm_name,)
            ).fetchone()
            if not perm:
                return False
            cur = conn.execute(
                "DELETE FROM user_permissions WHERE user_id = ? AND permission_id = ?",
                (user_id, perm["id"]),
            )
            return cur.rowcount > 0

    def get_user_direct_permissions(self, user_id: int) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT p.name FROM permissions p JOIN user_permissions up"
                " ON up.permission_id = p.id WHERE up.user_id = ? ORDER BY p.name",
                (user_id,),
            ).fetchall()
            return [r[0] for r in rows]

    def assign_role(self, user_id: int, role_name: str) -> bool:
        with self._connect() as conn:
            role = conn.execute("SELECT id FROM roles WHERE name = ?", (role_name,)).fetchone()
            if not role:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                (user_id, role["id"]),
            )
            return True

    def remove_role(self, user_id: int, role_name: str) -> bool:
        with self._connect() as conn:
            role = conn.execute("SELECT id FROM roles WHERE name = ?", (role_name,)).fetchone()
            if not role:
                return False
            cur = conn.execute(
                "DELETE FROM user_roles WHERE user_id = ? AND role_id = ?",
                (user_id, role["id"]),
            )
            return cur.rowcount > 0

    # -- roles & permissions ----------------------------------------------
    def list_roles(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            roles = [dict(r) for r in conn.execute("SELECT * FROM roles ORDER BY name")]
            for role in roles:
                perms = conn.execute(
                    "SELECT p.name FROM permissions p JOIN role_permissions rp"
                    " ON rp.permission_id = p.id WHERE rp.role_id = ? ORDER BY p.name",
                    (role["id"],),
                ).fetchall()
                role["permissions"] = [p[0] for p in perms]
            return roles

    def list_permissions(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM permissions ORDER BY name")]

    def grant_permission(self, role_name: str, perm_name: str) -> bool:
        with self._connect() as conn:
            role = conn.execute("SELECT id FROM roles WHERE name = ?", (role_name,)).fetchone()
            perm = conn.execute("SELECT id FROM permissions WHERE name = ?", (perm_name,)).fetchone()
            if not role or not perm:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                (role["id"], perm["id"]),
            )
            return True

    def revoke_permission(self, role_name: str, perm_name: str) -> bool:
        with self._connect() as conn:
            role = conn.execute("SELECT id FROM roles WHERE name = ?", (role_name,)).fetchone()
            perm = conn.execute("SELECT id FROM permissions WHERE name = ?", (perm_name,)).fetchone()
            if not role or not perm:
                return False
            cur = conn.execute(
                "DELETE FROM role_permissions WHERE role_id = ? AND permission_id = ?",
                (role["id"], perm["id"]),
            )
            return cur.rowcount > 0

    # -- admins ------------------------------------------------------------
    def create_admin_record(self, user_id: int, created_by: Optional[int], notes: str = "") -> Dict[str, Any]:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO admins (user_id, created_by_user_id, created_at, notes)"
                " VALUES (?, ?, ?, ?)",
                (user_id, created_by, _utcnow(), notes),
            )
            row = conn.execute("SELECT * FROM admins WHERE id = ?", (cur.lastrowid,)).fetchone()
            return dict(row) if row else {}

    def list_admins(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT a.*, u.name, u.email, u.status FROM admins a"
                " JOIN users u ON u.id = a.user_id ORDER BY a.id"
            )]

    def get_admin_by_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            return self._row_to_dict(
                conn.execute("SELECT * FROM admins WHERE user_id = ?", (user_id,)).fetchone()
            )

    def delete_admin_record(self, admin_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM admins WHERE id = ?", (admin_id,))
            return cur.rowcount > 0

    # -- settings & flags --------------------------------------------------
    def get_settings(self, include_secrets: bool = False) -> Dict[str, Any]:
        with self._connect() as conn:
            out: Dict[str, Any] = {}
            for row in conn.execute("SELECT key, value, is_secret FROM settings"):
                if row["is_secret"] and not include_secrets:
                    out[row["key"]] = "***"
                else:
                    try:
                        out[row["key"]] = json.loads(row["value"])
                    except Exception:
                        out[row["key"]] = row["value"]
            return out

    def set_setting(self, key: str, value: Any, updated_by: Optional[int], is_secret: bool = False) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value, is_secret, updated_at, updated_by)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " is_secret=excluded.is_secret, updated_at=excluded.updated_at,"
                " updated_by=excluded.updated_by",
                (key, json.dumps(value), 1 if is_secret else 0, _utcnow(), updated_by),
            )

    def get_flags(self) -> Dict[str, Any]:
        with self._connect() as conn:
            return {
                row["key"]: {"enabled": bool(row["enabled"]), "description": row["description"]}
                for row in conn.execute("SELECT key, enabled, description FROM feature_flags")
            }

    def set_flag(self, key: str, enabled: bool, updated_by: Optional[int]) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE feature_flags SET enabled = ?, updated_at = ?, updated_by = ? WHERE key = ?",
                (1 if enabled else 0, _utcnow(), updated_by, key),
            )
            return cur.rowcount > 0

    # -- tasks -------------------------------------------------------------
    def create_task(self, user_id: Optional[int], prompt: str) -> Dict[str, Any]:
        now = _utcnow()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO tasks (user_id, prompt, status, created_at, started_at)"
                " VALUES (?, ?, 'RUNNING', ?, ?)",
                (user_id, prompt, now, now),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
            return dict(row) if row else {"id": cur.lastrowid}

    def finish_task(self, task_id: int, status: str, result_summary: Optional[str] = None,
                    failure_reason: Optional[str] = None, approval_status: Optional[str] = None,
                    audit_reference: Optional[int] = None) -> None:
        if status not in TASK_STATUSES:
            status = "BLOCKED"
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, completed_at = ?, result_summary = ?,"
                " failure_reason = ?, approval_status = ?, audit_reference = ? WHERE id = ?",
                (status, _utcnow(), result_summary, failure_reason, approval_status, audit_reference, task_id),
            )

    def list_tasks(self, user_id: Optional[int] = None, status: Optional[str] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
        query = "SELECT * FROM tasks"
        clauses, params = [], []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(query, params)]

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            return self._row_to_dict(
                conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            )

    def count_tasks_by_status(self) -> Dict[str, int]:
        with self._connect() as conn:
            return {
                r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")
            }

    # -- audit -------------------------------------------------------------
    def record_audit(self, actor_user_id: Optional[int], event_type: str,
                     message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        details = details or {}
        with self._connect() as conn:
            last = conn.execute("SELECT hash FROM audit_logs ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash = last["hash"] if last else "0" * 64
            ts = _utcnow()
            canonical = json.dumps(
                {"timestamp": ts, "event_type": event_type, "message": message,
                 "details": details, "actor": actor_user_id, "prev_hash": prev_hash},
                sort_keys=True, separators=(",", ":"),
            )
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            cur = conn.execute(
                "INSERT INTO audit_logs (timestamp, actor_user_id, event_type, message, details, prev_hash, hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, actor_user_id, event_type, message, json.dumps(details), prev_hash, digest),
            )
            return dict(conn.execute("SELECT * FROM audit_logs WHERE id = ?", (cur.lastrowid,)).fetchone())

    def list_audit(self, event_type: Optional[str] = None, actor: Optional[int] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
        query = "SELECT * FROM audit_logs"
        clauses, params = [], []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if actor is not None:
            clauses.append("actor_user_id = ?")
            params.append(actor)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(query, params)]

    def verify_audit_chain(self) -> Dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM audit_logs ORDER BY id").fetchall()
        expected_prev = "0" * 64
        for row in rows:
            if row["prev_hash"] != expected_prev:
                return {"valid": False, "checked": row["id"], "total": len(rows)}
            canonical = json.dumps(
                {"timestamp": row["timestamp"], "event_type": row["event_type"],
                 "message": row["message"], "details": json.loads(row["details"] or "{}"),
                 "actor": row["actor_user_id"], "prev_hash": row["prev_hash"]},
                sort_keys=True, separators=(",", ":"),
            )
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != row["hash"]:
                return {"valid": False, "checked": row["id"], "total": len(rows)}
            expected_prev = row["hash"]
        return {"valid": True, "checked": len(rows), "total": len(rows)}

    # -- sessions ----------------------------------------------------------
    def create_session(self, user_id: int, token_hash: str, ttl_seconds: int = 43200) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen, revoked)"
                " VALUES (?, ?, ?, ?, ?, 0)",
                (token_hash, user_id, now, now + ttl_seconds, now),
            )

    def get_session(self, token_hash: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            return self._row_to_dict(
                conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
            )

    def touch_session(self, token_hash: str, ttl_seconds: int = 43200) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                (now, now + ttl_seconds, token_hash),
            )

    def revoke_session(self, token_hash: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (token_hash,))

    def revoke_user_sessions(self, user_id: int) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0", (user_id,)
            )
            return cur.rowcount

    # -- password resets ---------------------------------------------------
    def create_password_reset(self, user_id: int, token_hash: str, ttl_seconds: int = 3600) -> None:
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO password_resets (user_id, token_hash, created_at, expires_at, used)"
                " VALUES (?, ?, ?, ?, 0)",
                (user_id, token_hash, now, now + ttl_seconds),
            )

    def consume_password_reset(self, user_id: int, token_hash: str) -> bool:
        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM password_resets WHERE user_id = ? AND token_hash = ?"
                " AND used = 0 AND expires_at > ?",
                (user_id, token_hash, now),
            ).fetchone()
            if not row:
                return False
            conn.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (row["id"],))
            return True
