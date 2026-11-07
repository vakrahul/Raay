# ENGINEERING CODING STANDARDS
## Windows AI Operating Environment (WinAI-OE)
**Document Version:** 1.0.0  
**Phase:** Stage I — Phase 7 (Coding Standards)  
**Status:** Approved Standard  

---

## 1. Language & Naming Conventions

### 1.1 Python (Orchestrator, Security Core, Providers, Windows Integration)
* **Modules & Packages:** `lowercase_with_underscores.py` (e.g. `policy_engine.py`, `action_validator.py`).
* **Classes:** `PascalCase` (e.g. `BaseModelProvider`, `ExecutionPolicy`, `AuditLogger`).
* **Functions & Methods:** `snake_case()` (e.g. `evaluate_action()`, `verify_approval_nonce()`).
* **Variables & Arguments:** `snake_case` (e.g. `target_resource`, `max_tokens`).
* **Constants & Enums:** `UPPER_SNAKE_CASE` (e.g. `DEFAULT_TIMEOUT_SECONDS`, `RiskTier.HIGH`).
* **Type Annotations:** Mandatory on all public functions, class methods, and API models. Use `typing` and Pydantic v2 type hints.

### 1.2 C# / .NET (WinUI 3 Client)
* **Classes, Structs, Enums, Interfaces:** `PascalCase` (e.g. `ChatViewModel`, `IApprovalService`). Interfaces must start with `I`.
* **Methods & Properties:** `PascalCase` (e.g. `SendMessageAsync()`, `IsExecuting`).
* **Private Fields:** `_camelCase` (e.g. `_webSocketClient`, `_activeSessionId`).
* **Async Methods:** Must suffix with `Async` and accept a `CancellationToken` where feasible.

---

## 2. Error Handling & Fail-Closed Protocols

1. **No Silent Swallowing:** Never use bare `except:` or `catch (Exception) {}` without re-raising or logging with structured severity.
2. **Standard Exception Hierarchy:** All application errors must inherit from `WinAIError`:
   * `SecurityViolationError`: Attempted directory traversal, forbidden tool call, or signature mismatch.
   * `ValidationError`: Malformed input payload or schema mismatch.
   * `ProviderError`: LLM provider failure, rate limit, or timeout.
   * `ExecutionError`: Subprocess failure or timeout.
3. **Fail-Closed Semantics:** If an error occurs during policy evaluation, path resolution, or token verification, the system must default to `DENY` or `ABORT`. Never proceed on unhandled exceptions.
4. **Error Payloads:** Errors returned to clients must be structured:
   ```json
   {
     "error_code": "SEC_PATH_ESCAPE_DETECTED",
     "message": "Target path resolves outside authorized workspace boundary",
     "timestamp": "2026-09-20T17:00:00Z",
     "remediation": "Select a target file inside the current workspace root"
   }
   ```

---

## 3. Logging & Audit Standards

1. **Structured Format:** All application and security logs must be emitted in structured JSON / key-value format.
2. **Contextual Correlation:** Every log entry must include `timestamp` (ISO-8601 UTC), `session_id`, `agent_id` (if applicable), and `operation_id`.
3. **Secret Redaction:** Under no circumstances should API keys, bearer tokens, passwords, private keys, or raw DPAPI payloads be logged. Scrubbing filters must sanitize strings before logging.
4. **Tamper-Evident Security Log:** Security-critical decisions (Action Proposals, Approvals, Rejections, Policy Modifications) must be appended to the SHA-256 hash-chained audit log.

---

## 4. API & Data Contract Guidelines

1. **Strict Validation:** All incoming REST and WebSocket payloads must be validated using Pydantic models with `extra = "forbid"` to prevent unrecognized fields.
2. **Canonical Data Representation:** Filesystem paths must be canonicalized (`os.path.realpath`) before validation.
3. **Idempotency:** Action approvals and task cancellations must be idempotent.
4. **WebSocket Envelopes:** Real-time messages must use a consistent envelope:
   ```json
   {
     "event_type": "token_stream | tool_request | approval_prompt | task_state",
     "session_id": "string",
     "payload": {},
     "sequence_number": 12
   }
   ```

---

## 5. Security & Isolation Rules

1. **Process Spawning:** Commands must execute with `shell=False` and explicit argument arrays (`["git", "status"]`). Never interpolate untrusted strings into command lines.
2. **Job Object Containment:** Any subprocess must be registered with a Windows Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
3. **Untrusted Inputs:** All model completions, external documents, and tool outputs are untrusted. They must be validated against schemas before being acted upon.
4. **CSPRNG Nonces:** Nonces for human approval must be generated with cryptographically secure random generators (`secrets.token_hex(32)`).

---

## 6. Code Review & Testing Expectations

1. Every pull request or phase deliverable must include automated unit or integration tests.
2. Static analysis checks (`ruff check src tests`) must pass with 0 errors.
3. No secrets or credentials may be committed (verified by pre-commit / automated scanning).
4. Code coverage target: Minimum 80% on core security, policy, and provider adapter components.
