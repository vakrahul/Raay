"""FastAPI AI Orchestrator service (Zone 2).

Provides loopback REST and WebSocket interfaces for desktop client communication,
mediating between UI, AI providers, and host security policies.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.orchestrator.config import AppConfig, get_config
from src.providers.base import BaseModelProvider, ChatMessage, ProviderResponse
from src.providers.registry import ProviderRegistry
from src.security.policy_engine import (
    ActionEvaluationResult,
    PolicyDecision,
    SecurityPolicyEngine,
)
from src.storage.credential_vault import CredentialVault
from src.windows_integration.system_app_scanner import SystemAppScanner
from src.windows_integration.app_manager import AppManager
from src.orchestrator.task_dispatcher import AutonomousTaskDispatcher
from src.platform.agent_config import extract_requested_apps, get_effective_agent_config
from src.platform.control_db import ControlPlaneDB
from src.platform.routes import router as platform_router
from src.security.audit_logger import AuditLogger
from src.windows_integration.execution_engine import ActionExecutionOutcome, WindowsExecutionEngine

import time as _time

_APP_START_TIME = _time.time()


class LaunchAppPayload(BaseModel):
    app_id: str


class TaskDispatchPayload(BaseModel):
    task: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 1024


class ApprovalResponsePayload(BaseModel):
    approval_nonce: str
    user_decision: str = Field(pattern="^(APPROVED|DENIED)$")


async def _optional_user_id(request: Request, db: ControlPlaneDB) -> Optional[int]:
    """Best-effort session resolution for dispatch attribution; anonymous when absent."""
    from src.platform.auth import get_current_user

    if not request.headers.get("authorization"):
        return None
    try:
        user = await get_current_user(request)
        return user["id"]
    except Exception:
        return None


_STATUS_MAP = {
    "COMPLETED": "COMPLETED",
    "FAILED": "FAILED",
    "NEEDS_CLARIFICATION": "NEEDS_CLARIFICATION",
    "PARTIAL": "PARTIAL",
    "BLOCKED": "BLOCKED",
}


def _record_dispatch(db: ControlPlaneDB, user_id: Optional[int], prompt: str,
                     result: dict) -> None:
    """Persist dispatch history + audit trail. Never raises into the request path."""
    try:
        row = db.create_task(user_id, prompt)
        status = _STATUS_MAP.get((result.get("status") or "").upper(), "BLOCKED")
        summary = str(result.get("summary") or "")[:500]
        failure = None if status == "COMPLETED" else summary
        db.finish_task(row["id"], status,
                       result_summary=summary if status == "COMPLETED" else None,
                       failure_reason=failure)
        db.record_audit(user_id, "TASK_CREATED", f"Task dispatched (status {status})",
                        {"task_id": row["id"]})
        if status == "COMPLETED":
            db.record_audit(user_id, "TASK_COMPLETED", f"Task {row['id']} completed", {"task_id": row["id"]})
        elif status in ("FAILED", "BLOCKED"):
            db.record_audit(user_id, "TASK_FAILED", f"Task {row['id']} ended as {status}",
                            {"task_id": row["id"], "reason": (failure or "")[:200]})
    except Exception:
        pass


def create_app(config: Optional[AppConfig] = None) -> FastAPI:
    """Application factory for FastAPI orchestrator."""
    app_config = config or get_config()
    app = FastAPI(
        title=app_config.app_name,
        version=app_config.app_version,
        docs_url="/docs",
    )

    # Initialize subsystems
    policy_engine = SecurityPolicyEngine(
        workspace_root=app_config.workspace_root,
        require_approvals=(app_config.security_level.value == "strict"),
    )
    
    vault = CredentialVault()
    provider_registry = ProviderRegistry(vault=vault)
    provider_registry.initialize_from_vault()

    def get_target_provider(requested_provider: Optional[str] = None) -> BaseModelProvider:
        if requested_provider:
            return provider_registry.get_provider(requested_provider)
        # Database-backed default provider (falls back to legacy logic).
        try:
            configured = get_effective_agent_config(
                request_state_db()
            ).get("default_provider")
            if configured and configured in provider_registry.list_providers():
                return provider_registry.get_provider(configured)
        except Exception:
            pass
        if app_config.environment == "testing":
            return provider_registry.get_provider(app_config.default_provider)
        if "gemini" in provider_registry._providers:
            return provider_registry.get_provider("gemini")
        return provider_registry.get_provider(app_config.default_provider)

    def request_state_db() -> ControlPlaneDB:
        return app.state.control_db

    @app.get("/health")
    async def health_check():
        active_prov = get_target_provider()
        provider_healthy = await active_prov.health_check()
        return build_health_payload(
            provider_healthy=provider_healthy,
            app_name=app_config.app_name,
            app_version=app_config.app_version,
            environment=app_config.environment,
            provider_name=active_prov.get_capabilities().provider_name,
            configured_providers=provider_registry.list_providers(),
        )

    app_scanner = SystemAppScanner()
    app_manager = AppManager()

    from src.orchestrator.token_optimizer import TokenOptimizer
    token_optimizer = TokenOptimizer()

    from src.orchestrator.jev.service import JevService

    jev_service = JevService()

    class JevConfigPayload(BaseModel):
        enabled: bool

    class JevTestPayload(BaseModel):
        context: str = Field(default="Summarize the quarterly notes.", min_length=1, max_length=2000)

    @app.get("/api/v1/jev/status")
    async def jev_status():
        return await jev_service.status_dict()

    @app.get("/api/v1/jev/metrics")
    async def jev_metrics():
        return jev_service.metrics_dict()

    @app.get("/api/v1/jev/decisions")
    async def jev_decisions(limit: int = 10):
        return {"decisions": jev_service.recent_decisions(max(1, min(limit, 50)))}

    @app.get("/api/v1/telemetry/tokens")
    async def get_token_telemetry():
        return token_optimizer.get_telemetry_summary()

    @app.post("/api/v1/jev/config")
    async def jev_config(payload: JevConfigPayload):
        return {"enabled": jev_service.set_enabled(payload.enabled)}

    @app.post("/api/v1/jev/test")
    async def jev_test(payload: JevTestPayload):
        return await jev_service.test_decision(payload.context)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    @app.get("/cockpit", response_class=HTMLResponse)
    async def get_dashboard():
        html_path = Path(__file__).parent / "dashboard.html"
        if html_path.exists():
            return html_path.read_text(encoding="utf-8")
        return "<h1>WinAI Dashboard Active</h1>"

    # Dynamic control-plane UI (static assets + guarded pages; API does auth).
    from fastapi.staticfiles import StaticFiles

    _ui_dir = Path(__file__).parent / "ui"
    if _ui_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=str(_ui_dir)), name="control-ui")

    def _serve_ui_page(name: str) -> str:
        path = Path(__file__).parent / "ui" / name
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "<h1>Control-plane UI not installed</h1>"

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        return _serve_ui_page("login.html")

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page():
        return _serve_ui_page("admin.html")

    @app.get("/user", response_class=HTMLResponse)
    async def user_page():
        return _serve_ui_page("user.html")

    @app.get("/api/v1/system/apps")
    async def get_system_apps():
        apps = app_scanner.scan_all_applications()
        stats = app_scanner.get_summary_stats()
        return {"stats": stats, "applications": [a.model_dump() for a in apps]}

    @app.post("/api/v1/system/apps/launch")
    async def launch_application(payload: LaunchAppPayload):
        try:
            pid = app_manager.launch_app(payload.app_id)
            return {"status": "success", "app_id": payload.app_id, "pid": pid}
        except PermissionError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(e),
            ) from e
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to launch approved application '{payload.app_id}': {e}",
            ) from e

    @app.get("/api/v1/config")
    async def get_system_config():
        return app_config.public_config_dict()

    dispatcher = AutonomousTaskDispatcher(
        workspace_root=app_config.workspace_root,
        policy_engine=policy_engine,
    )

    from src.voice.api_routes import VoiceCockpitService, create_voice_router

    voice_service = VoiceCockpitService(
        workspace_root=app_config.workspace_root,
        policy_engine=policy_engine,
        dispatcher=dispatcher,
        provider_registry=provider_registry,
        vault=vault,
        allow_vault_lookup=(app_config.environment != "testing"),
    )
    app.state.voice_service = voice_service
    app.include_router(create_voice_router(voice_service))

    # Dynamic control plane: database-backed auth/RBAC/settings/tasks/audit.
    # Lives in its own module; the automation engine below is untouched.
    app.state.control_db = ControlPlaneDB(app_config.workspace_root)
    app.include_router(platform_router)

    @app.get("/api/v1/app/identity")
    async def app_identity():
        """Self-reported identity of the running WinAI-OE application process.

        Reports only non-sensitive process facts: PID, executable, user,
        elevation state, and subsystem initialization. Never secrets.
        """
        import ctypes
        import getpass
        import os
        import sys
        import time

        try:
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            elevated = False
        try:
            username = getpass.getuser()
        except Exception:
            username = "unknown"

        engine = dispatcher.execution_engine
        services = {
            "policy_engine": isinstance(
                engine.policy_engine, SecurityPolicyEngine
            ),
            "execution_engine": engine is not None,
            "uia_service": getattr(engine, "uia_service", None) is not None,
            "system_intelligence": getattr(engine, "intelligence", None) is not None,
            "process_manager": getattr(engine, "process_manager", None) is not None,
            "os_context": getattr(engine, "os_context", None) is not None,
            "audit_logger": getattr(engine, "audit_logger", None) is not None,
            "app_manager_approved_apps": len(engine.app_manager.list_approved_apps())
            if getattr(engine, "app_manager", None) else 0,
        }
        return {
            "process_id": os.getpid(),
            "executable_path": sys.executable,
            "entry_script": os.path.abspath(sys.argv[0]) if sys.argv else None,
            "username": username,
            "elevated_privileges": elevated,
            "privilege_note": "normal user"
            if not elevated
            else "ELEVATED (administrator)",
            "security_policy_mode": "approvals_required"
            if engine.policy_engine.require_approvals
            else "ambient_allow_per_policy",
            "services_initialized": services,
            "all_services_ok": all(
                v for k, v in services.items() if k != "app_manager_approved_apps"
            ),
            "server_uptime_seconds": round(time.time() - _APP_START_TIME, 1),
        }

    @app.post("/api/v1/tasks/dispatch")
    async def dispatch_task(payload: TaskDispatchPayload, request: Request):
        db: ControlPlaneDB = request.app.state.control_db
        user_id = await _optional_user_id(request, db)

        # Agent configuration gate: fresh DB read on every dispatch.
        try:
            agent_cfg = get_effective_agent_config(db)
        except Exception:
            agent_cfg = {"agent_enabled": True, "allowed_applications": []}
        if not agent_cfg.get("agent_enabled", True):
            blocked = {
                "action_type": "agent_disabled",
                "summary": "Agent execution is disabled by system settings.",
                "status": "BLOCKED",
                "details": {},
                "observable_evidence": ["agent_enabled=false in database settings"],
            }
            _record_dispatch(db, user_id, payload.task, blocked)
            return blocked
        allowed = set(agent_cfg.get("allowed_applications") or [])
        for requested in extract_requested_apps(payload.task):
            if requested in ("notepad", "calc", "chrome", "vscode", "edge") and requested not in allowed:
                blocked = {
                    "action_type": "app_not_allowed",
                    "summary": f"Application '{requested}' is disabled by system settings.",
                    "status": "BLOCKED",
                    "details": {"requested_app": requested, "allowed_applications": sorted(allowed)},
                    "observable_evidence": ["allowed_applications gate in database settings"],
                }
                _record_dispatch(db, user_id, payload.task, blocked)
                return blocked

        result = await dispatcher.execute_task(payload.task)
        dumped = result.model_dump()
        _record_dispatch(db, user_id, payload.task, dumped)
        return dumped

    @app.post("/api/v1/security/approval-selftest")
    async def approval_selftest():
        """Exercise the real approval lifecycle against a harmless probe file.

        Uses a STRICT policy engine (approvals required) and the real execution
        engine + audit log. Transcript proves: pre-approval non-execution,
        invalid-nonce rejection, single-use valid approval, reuse rejection.
        """
        import time as _t

        t0 = _t.perf_counter()
        probe_rel = ".winai/approval_probe.txt"
        probe_abs = (app_config.workspace_root / probe_rel).resolve()
        if probe_abs.exists():
            probe_abs.unlink()

        strict_policy = SecurityPolicyEngine(
            workspace_root=app_config.workspace_root, require_approvals=True
        )
        strict_audit = AuditLogger(log_file=app_config.workspace_root / ".winai" / "audit.log")
        strict_engine = WindowsExecutionEngine(
            workspace_root=app_config.workspace_root,
            policy_engine=strict_policy,
            audit_logger=strict_audit,
        )
        transcript = []
        sid, agent = "approval_selftest", "security_harness"

        # 1. Request approval for a harmless write.
        r1 = await strict_engine.execute_action(
            tool_name="fs_write_file",
            arguments={"path": probe_rel, "content": "approvallifecycle-probe"},
            session_id=sid,
            agent_id=agent,
        )
        nonce = (r1.post_state or {}).get("approval_nonce")
        transcript.append({
            "stage": "request_approval",
            "outcome": r1.outcome.value,
            "nonce_issued": bool(nonce),
            "file_exists_after_request": probe_abs.exists(),
        })

        # 2. Invalid nonce must be rejected with nothing executed.
        r2 = await strict_engine.execute_action(
            tool_name="fs_write_file",
            arguments={"path": probe_rel, "content": "approvallifecycle-probe"},
            session_id=sid,
            agent_id=agent,
            approval_nonce="deadbeef" * 8,
        )
        transcript.append({
            "stage": "invalid_nonce",
            "outcome": r2.outcome.value,
            "rejected": r2.outcome == ActionExecutionOutcome.BLOCKED_POLICY,
            "file_exists_after_invalid": probe_abs.exists(),
        })

        # 3. Valid nonce executes exactly the approved action.
        r3 = await strict_engine.execute_action(
            tool_name="fs_write_file",
            arguments={"path": probe_rel, "content": "approvallifecycle-probe"},
            session_id=sid,
            agent_id=agent,
            approval_nonce=nonce,
        )
        try:
            content_ok = probe_abs.read_text(encoding="utf-8") == "approvallifecycle-probe"
        except Exception:
            content_ok = False
        transcript.append({
            "stage": "valid_nonce_single_use",
            "outcome": r3.outcome.value,
            "file_exists": probe_abs.exists(),
            "content_verified": content_ok,
        })
        mtime_after_valid = probe_abs.stat().st_mtime if probe_abs.exists() else 0.0

        # 4. Reused nonce must be rejected; file must be untouched.
        r4 = await strict_engine.execute_action(
            tool_name="fs_write_file",
            arguments={"path": probe_rel, "content": "tampered-content"},
            session_id=sid,
            agent_id=agent,
            approval_nonce=nonce,
        )
        try:
            content_still_ok = probe_abs.read_text(encoding="utf-8") == "approvallifecycle-probe"
        except Exception:
            content_still_ok = False
        transcript.append({
            "stage": "reused_nonce",
            "outcome": r4.outcome.value,
            "rejected": r4.outcome == ActionExecutionOutcome.BLOCKED_POLICY,
            "file_untouched": content_still_ok,
            "mtime_unchanged": (probe_abs.stat().st_mtime == mtime_after_valid) if probe_abs.exists() else False,
        })

        passed = (
            r1.outcome == ActionExecutionOutcome.BLOCKED_APPROVAL
            and not transcript[0]["file_exists_after_request"]
            and transcript[1]["rejected"]
            and not transcript[1]["file_exists_after_invalid"]
            and r3.outcome == ActionExecutionOutcome.VERIFIED_SUCCESS
            and transcript[2]["content_verified"]
            and transcript[3]["rejected"]
            and transcript[3]["file_untouched"]
        )
        return {
            "status": "PASSED" if passed else "FAILED",
            "probe_file": str(probe_abs),
            "transcript": transcript,
            "duration_ms": round((_t.perf_counter() - t0) * 1000, 1),
        }

    @app.post("/api/v1/chat", response_model=ProviderResponse)
    async def complete_chat(request: ChatRequest):
        provider = get_target_provider(request.provider)
        response, _ = await token_optimizer.execute_optimized_request(
            provider=provider,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            model_name=request.model,
        )
        return response

    @app.get("/api/v1/approval/pending")
    async def list_pending_approvals():
        """List awaiting approval requests for the on-screen approval button.

        Loopback-only like every other route (see host binding). The overlay
        polls this to render its badge.
        """
        return {"pending": policy_engine.pending_approvals_summary()}

    @app.post("/api/v1/approval/respond")
    async def respond_to_approval(payload: ApprovalResponsePayload):
        pending = policy_engine.consume_approval(payload.approval_nonce)
        if not pending:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invalid or expired approval nonce",
            )
        return {
            "status": "processed",
            "decision": payload.user_decision,
            "action": pending,
        }

    @app.websocket("/ws/v1/stream")
    async def websocket_stream_endpoint(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_text()
                message_json = json.loads(data)
                action = message_json.get("action")

                if action == "chat":
                    raw_messages = message_json.get("messages", [])
                    req_prov = message_json.get("provider")
                    provider = get_target_provider(req_prov)
                    messages = [ChatMessage(**m) for m in raw_messages]

                    # 1. Stream tokens
                    async for token in provider.stream(messages):
                        await websocket.send_json({
                            "event": "token",
                            "data": token,
                        })

                    # 2. Check if model proposes a tool
                    complete_resp = await provider.complete(messages)
                    if complete_resp.tool_calls:
                        for tool_call in complete_resp.tool_calls:
                            # Evaluate through Policy Engine
                            eval_result = policy_engine.evaluate_action(
                                tool_name=tool_call.tool_name,
                                arguments=tool_call.arguments,
                                session_id="ws_session",
                                agent_id="agent_stream",
                            )

                            await websocket.send_json({
                                "event": "tool_proposal",
                                "tool_call": tool_call.model_dump(),
                                "policy_decision": eval_result.model_dump(),
                            })

                    await websocket.send_json({"event": "done"})

                elif action == "cancel":
                    await websocket.send_json(build_cancelled_event())

        except WebSocketDisconnect:
            pass

    return app


WS_OUTBOUND_EVENTS = ("token", "tool_proposal", "done", "cancelled")
WS_INBOUND_ACTIONS = ("chat", "cancel")


def is_known_ws_event(event: str) -> bool:
    """Return True for protocol-defined outbound websocket events."""
    return event in WS_OUTBOUND_EVENTS


def build_cancelled_event() -> dict:
    """Build the idempotent cancellation acknowledgement frame."""
    return {"event": "cancelled"}


def build_health_payload(
    *,
    provider_healthy: bool,
    app_name: str,
    app_version: str,
    environment: Any,
    provider_name: str,
    configured_providers: list,
) -> dict:
    """Build the GET /health payload (degraded on provider outage, never secret-bearing)."""
    return {
        "status": "healthy" if provider_healthy else "degraded",
        "app": app_name,
        "version": app_version,
        "environment": environment,
        "provider": provider_name,
        "provider_healthy": provider_healthy,
        "configured_providers": configured_providers,
    }


def list_registered_routes(app: FastAPI) -> list:
    """Return sorted (path, methods) pairs for the registered HTTP routes."""
    entries = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = sorted(getattr(route, "methods", set()) or set())
        if path:
            entries.append((path, methods))
    return sorted(entries)


STARTUP_ORDER = (
    "config",
    "security_policy",
    "credential_vault",
    "provider_registry",
    "system_scanner",
    "app_manager",
    "routes",
    "task_dispatcher",
)


def describe_startup_order() -> tuple:
    """Return the normative subsystem initialization order (no side effects)."""
    return STARTUP_ORDER


app = create_app()
