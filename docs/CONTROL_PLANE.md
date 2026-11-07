# WinAI-OE Dynamic Control Plane

Database-backed product layer on top of the existing FastAPI orchestrator
(`src/orchestrator/main.py`). No automation code was replaced: the Windows
execution engine, policy engine, and audit logger are used as-is; this layer
only adds identity, authorization, configuration, history, and management UI.

## 1. Setup / bootstrap

1. Start the app (`python run_vertical_slice.py` or the packaged exe). A fresh
   SQLite database is created automatically at `<workspace>/.winai/control_plane.db`
   (WAL mode) with roles, permissions, settings, and feature flags seeded.
2. Create the one initial Super Admin (allowed **only** while zero users exist):
   `POST /auth/bootstrap {"name","email","password"}` (min 10 chars) → returns
   a session token + user object.
3. Open `/login` in a browser, sign in, and manage everything from `/admin`
   (Super Admin / Admin) or `/user` (standard users).

No credentials exist in source code. Passwords are PBKDF2-SHA256 (600k
iterations, per-user 32-byte salt); sessions are opaque 48-byte tokens
(SHA-256 stored, 12h sliding expiry).

## 2. Database schema

Tables (FKs enforced, indexes on hot paths): `schema_migrations`,
`users`, `roles`, `permissions`, `role_permissions`, `user_roles`,
`user_permissions` (direct grants), `admins`, `settings` (`is_secret` flag),
`feature_flags`, `tasks`, `audit_logs` (hash-chained, same canonical-JSON
approach as `.winai/audit.log`), `sessions`, `password_resets`.

Migrations in `src/platform/control_db.py` (`_migrate_v1` schema,
`_migrate_v2_seed` roles/permissions/settings/flags). Idempotent: a second
app instance over the same file picks up all state (verified by test).

## 3. API reference (all JSON; auth = `Authorization: Bearer <token>`)

| Method & path | Permission | Purpose |
|---|---|---|
| POST `/auth/bootstrap` | none (fresh DB only, else 403) | Create initial Super Admin |
| POST `/auth/login` | none (rate-limited) | Login → token + user |
| POST `/auth/logout` | session | Revoke current session |
| GET `/me` | session | Own profile + live roles/permissions |
| PATCH `/me/config` | manage_own_settings | Update own JSON configuration |
| POST `/auth/password-reset/request` | none (rate-limited) | Generic response; token stored hashed (email delivery out of scope — see §6) |
| POST `/auth/password-reset/confirm` | none (rate-limited) | Redeem token, set password, revoke sessions |
| GET `/admins` | create_admin | List admins + roles + direct grants |
| POST `/admins` | create_admin | Create admin (password optional → generated once) |
| PATCH `/admins/{id}` | create_admin | Rename / enable / disable (self-disable blocked) |
| DELETE `/admins/{id}` | delete_admin | Delete admin+user (self + last-super-admin blocked) |
| PATCH `/admins/{id}/password` | create_admin | Set or auto-generate password, revoke sessions |
| POST `/admins/{id}/permissions` | manage_permissions | Direct grant |
| DELETE `/admins/{id}/permissions/{p}` | manage_permissions | Direct revoke |
| GET `/users` | view_users | List users |
| POST `/users` | manage_users | Create user (role USER\|ADMIN) |
| PATCH `/users/{id}` | manage_users (+manage_permissions for roles) | Edit / disable / assign roles (SUPER_ADMIN grant needs super) |
| GET `/roles`, GET `/permissions` | session | Read role matrix |
| GET `/settings` | session | Settings (secrets masked) + flags |
| PATCH `/settings` | manage_system_settings (or assigned subset) | Update settings/flags; unknown keys → 422 |
| PATCH `/settings/secrets` | manage_system_settings | Store masked secret |
| GET `/tasks[?status&limit&mine]` | session (own unless view_usage) | Task history |
| GET `/tasks/{id}` | session (visibility-scoped, 404 otherwise) | Task detail |
| GET `/audit[?event_type&limit]` | view_system_logs | Audit trail |
| GET `/audit/verify` | view_system_logs | Hash-chain verification |
| GET `/api/v1/agent/config` | use_agent | Live effective agent config |

Every dispatch via `POST /api/v1/tasks/dispatch` is recorded (RUNNING → final
status) with `TASK_CREATED` (+`TASK_COMPLETED`/`TASK_FAILED`) audit rows, for
authenticated and anonymous callers alike.

## 4. Agent configuration interface

`src/platform/agent_config.py :: get_effective_agent_config(db)` resolves
`agent_enabled`, `default_provider`, `default_model`,
`max_task_duration_seconds`, `approval_policy`, `log_level`,
`allowed_applications`, and feature flags from the DB on **every call**.
`main.py` enforces two gates before dispatching (disabled agent → BLOCKED;
disallowed app → BLOCKED) and falls back to the DB `default_provider` for
chat when the caller expresses no preference. No restarts needed.

## 5. UI

`/login` (sign-in + role-aware redirect), `/admin` (Dashboard with live
counts, Admins, Users, Roles matrix, Settings + secrets, Agent Config,
Tasks, Audit with chain badge), `/user` (own tasks, own JSON settings).
Shared `/ui/app.js` (auth guard, API client, toasts, confirm dialogs) and
`/ui/styles.css`. All data comes from the APIs above; empty states shown
instead of fake numbers.

## 6. Limitations (honest)

- Password-reset **email delivery is not implemented** (no SMTP configured);
  reset tokens are stored hashed and redeemable via the confirm endpoint;
  the supported UI flow is admin password reset. Tests exercise the confirm
  path white-box.
- The approval overlay (`/api/v1/approval/*`) and WebSocket chat flows are
  unchanged and still use the in-memory policy engine, not DB sessions.
- `allowed_applications` gates dispatch for the 5 whitelisted desktop apps;
  finer per-user app scoping is future work.
- Rate limiting is in-memory per process (fine for a loopback single-user
  service; not a distributed limiter).

## 7. Verification report

`tests/integration/test_control_plane.py` (10 tests, all passing 2026-09-23):
bootstrap-once, login/logout/invalid sessions, full §16 dynamic flow
(create Admin A → login → grant perm → create User A → isolation → revoke →
disable blocks login), role-grant permission check, self-protection,
settings+flags persistence across a fresh app instance on the same DB,
assigned-settings allowlist, agent disable/allowed-apps gates + task
history, password-reset confirm, audit verify endpoint, UI pages serve.
Regression: `test_vertical_slice.py`, `test_phase_0180_render_signoff.py`,
`test_audit_logger.py`, `test_approval_overlay.py`, `test_token_optimizer.py`
(19 tests) all pass.
