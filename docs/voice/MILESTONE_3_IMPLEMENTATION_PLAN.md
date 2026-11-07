# Milestone 3 Implementation Plan — Voice Intent Routing & Controlled Execution

**Status:** Active Implementation Plan (Milestone 3 Only)  
**Scope:** Security Gate remediation (`AutonomousTaskDispatcher` + action-bound `SecurityPolicyEngine` approvals), strict structured `VoiceIntent` schema and 5-action allowlist, `VoiceIntentPlanner`, `VoiceExecutionCoordinator` end-to-end pipeline, sanitized `AuditLogger` integration, and deterministic security/pipeline/e2e tests.

---

## 1. Repository Audit Findings & Security Gaps Identified

### 1.1 Policy Engine Construction Audit
We inspected every construction of `SecurityPolicyEngine` across `src/`:
1. `src/orchestrator/main.py` (line 113): Constructs `policy_engine = SecurityPolicyEngine(workspace_root=app_config.workspace_root, require_approvals=(app_config.security_level.value == "strict"))`.
2. **`src/orchestrator/task_dispatcher.py` (line 75 — CRITICAL BYPASS FOUND):**
   - `AutonomousTaskDispatcher.__init__` unconditionally created its own internal `SecurityPolicyEngine(workspace_root=self.workspace_root, require_approvals=False)`.
   - `src/orchestrator/main.py` (line 249) instantiated `AutonomousTaskDispatcher(workspace_root=app_config.workspace_root)` without injecting `policy_engine`, causing dispatched tasks to bypass `require_approvals=True`.
   - Furthermore, `AutonomousTaskDispatcher.execute_desktop_workflow` (line 898) contained a fallback that called `self.execution_engine.file_service.write_file(...)` directly when Notepad GUI save did not create the file, bypassing `execution_engine.execute_action("fs_write_file", ...)`.
3. **`src/security/policy_engine.py` & `src/windows_integration/execution_engine.py` (APPROVAL BINDING GAP FOUND):**
   - `SecurityPolicyEngine.consume_approval(nonce)` previously popped `nonce` from `_pending_approvals` without verifying TTL expiration, `tool_name`, `session_id`, or a cryptographic hash of `arguments` (`action_hash`). If arguments were modified between approval issuance and execution, `WindowsExecutionEngine.execute_action()` would have accepted the nonce.

### 1.2 Mandatory Security Gate Remediations (Phase B)
1. **`AutonomousTaskDispatcher` Policy Injection & Fail-Closed Default:**
   - Update `AutonomousTaskDispatcher.__init__` to accept `policy_engine: Optional[SecurityPolicyEngine] = None`, `audit_logger: Optional[AuditLogger] = None`, and default `require_approvals: bool = True` when constructing a fallback `SecurityPolicyEngine`.
   - Update `src/orchestrator/main.py` (line 249) to pass the authoritative `policy_engine` into `AutonomousTaskDispatcher(workspace_root=app_config.workspace_root, policy_engine=policy_engine)`.
   - Route the fallback file write in `execute_desktop_workflow` through `self.execution_engine.execute_action("fs_write_file", ...)` so no code path writes to disk without policy evaluation.
2. **Action-Bound & Argument-Bound Approval Nonces in `SecurityPolicyEngine` and `WindowsExecutionEngine`:**
   - Add `SecurityPolicyEngine.compute_action_hash(tool_name, arguments, session_id)` (SHA-256 over canonical sorted JSON).
   - Store `arguments`, `action_hash`, `created_at`, and `ttl_seconds` (300s) in `_pending_approvals[nonce]`.
   - Enhance `SecurityPolicyEngine.consume_approval()` to validate TTL and, when `expected_tool_name`, `expected_arguments`, or `expected_session_id` are supplied (as `WindowsExecutionEngine.execute_action()` now does), reject any nonce whose tool, arguments, or session do not match the originally approved request.

---

## 2. Architecture & Trust Boundaries

```
[Microphone / Audio]
        │  (Explicit user start via SpeechInputManager)
        ▼
[GnaniSTTProvider (Prisma v2.5)]
        │  (Untrusted final transcript; bounded length <= 2,000 chars)
        ▼
[Transcript Validator & DynamicDefenseGuard]
        │  (Rejects empty/oversized transcripts and prompt/command injections)
        ▼
[VoiceIntentPlanner (Untrusted Advisory Interpreter)]
        │  (Outputs raw JSON candidate; model-supplied approval/risk flags stripped)
        ▼
[Strict VoiceIntent Schema Validator (src/voice/intent_schema.py)]
        │  (Enforces 5-action allowlist, extra="forbid", path/app argument validation,
        │   and computes trusted host-side risk_category & requires_confirmation)
        ▼
[Authoritative SecurityPolicyEngine + ApprovalBroker]
        │  ├─ DENY ──> Block immediately, log sanitized audit event, return denial
        │  ├─ REQUIRE_APPROVAL (no nonce) ──> Issue action-bound ApprovalRequest, return pending
        │  └─ ALLOW or Validated Nonce ──> Revalidate policy & session cancellation state
        ▼
[Authorized Execution via WindowsExecutionEngine / AutonomousTaskDispatcher]
        │  (Executes only the validated allowlisted tool within workspace boundaries)
        ▼
[Result Validator & Sanitized AuditLogger]
        │  (Logs request_id, session_id, action_type, policy_decision, approval/exec outcome)
        ▼
[GnaniTTSProvider (Timbre) + SpeechOutputManager]
        └─ (Speaks concise result summary; if TTS/speaker fails, returns text result
            without re-executing the completed action)
```

---

## 3. Initial 5-Action Allowlist & Strict Intent Schema (`src/voice/intent_schema.py`)

Only 5 deterministic, existing low-risk actions are permitted in Milestone 3:

| Allowed `action_type` | Mapped Engine Tool | Validated Arguments | Filesystem / App Boundary |
|---|---|---|---|
| `system_info` | `system_get_overview` | `{}` (or `{"detailed": bool}`) | Read-only OS hardware/RAM telemetry (`ALLOW`). |
| `list_approved_apps` | `app_list_approved` | `{}` | Read-only query of `AppManager.list_approved_apps()` (`ALLOW`). |
| `launch_app` | `app_launch` | `{"app_id": str}` (normalized lowercase, alphanumeric/underscore, max 32 chars) | Must be in `AppManager` whitelist (`notepad`, `calc`, `chrome`, `vscode`, `edge`) and evaluated by `SecurityPolicyEngine`. |
| `read_workspace_file` | `fs_read_file` | `{"path": str, "max_bytes": int}` | Relative workspace path only; `../`, absolute drives, UNC paths, ADS (`:`), and null bytes rejected; verified by `SecurityPolicyEngine.is_path_confined()`. |
| `create_workspace_file` | `fs_write_file` | `{"path": str, "content": str}` | Relative workspace path only; **must not overwrite an existing file** (`target.exists()` rejected before execution unless explicitly using a new filename); evaluated by `SecurityPolicyEngine` (`REQUIRE_APPROVAL` in strict mode). |

**Explicitly Excluded Actions (Rejected at Intent Validation Gate):**
- `terminal_run`, `cmd_exec`, PowerShell/CMD commands, scripts, `fs_delete_file`, file overwrite, `process_close`, `deploy_project`, browser/email/outreach automation, and paths targeting `Desktop`, `Documents`, `Downloads`, or system directories.

**Untrusted Model Output Sanitization:**
- If the LLM/planner includes `"user_approved"`, `"approved"`, `"approval_nonce"`, `"bypass_policy"`, `"risk_category"`, or `"requires_confirmation"` in its JSON output, the validator strips/ignores those fields before schema validation and computes `risk_category` and `requires_confirmation` deterministically in host code.

---

## 4. Proposed Files to Create or Modify

### Files to Modify (Phase B Security Gate)
1. `src/security/policy_engine.py` — Add `compute_action_hash()`, TTL tracking, and action/argument/session binding verification in `consume_approval()`.
2. `src/windows_integration/execution_engine.py` — Pass `expected_tool_name`, `expected_arguments`, and `expected_session_id` to `policy_engine.consume_approval()`.
3. `src/orchestrator/task_dispatcher.py` — Accept injected `policy_engine` and `audit_logger`, default `require_approvals=True`, and remove the unguarded `file_service.write_file()` bypass in `execute_desktop_workflow`.
4. `src/orchestrator/main.py` — Inject the application's authoritative `policy_engine` into `AutonomousTaskDispatcher`.
5. `src/voice/__init__.py` — Export Milestone 3 classes and schemas.

### Files to Create (Phases C–H)
1. `src/voice/intent_schema.py` — `AllowedVoiceAction`, `VoiceIntent`, `IntentValidationError`, `validate_untrusted_intent()`, and `VoiceIntentPlanner`.
2. `src/voice/pipeline.py` — `VoicePipelineStatus`, `VoicePipelineResult`, and `VoiceExecutionCoordinator`.
3. `tests/unit/test_voice_milestone3.py` — Comprehensive deterministic security, approval-binding, pipeline, cancellation, and mocked `"Open Notepad"` end-to-end tests.
4. `docs/voice/MILESTONE_3_IMPLEMENTATION_PLAN.md` — This implementation plan.
5. `docs/voice/MILESTONE_3_IMPLEMENTATION_REPORT.md` — Final completion report.
