# TRUST BOUNDARY SPECIFICATION
## Windows AI Operating Environment (WinAI-OE)
**Document Version:** 1.0.0  
**Phase:** Stage I — Phase 4 (Trust Boundaries)  
**Status:** Approved Specification  

---

## 1. Architectural Trust Hierarchy

The Windows AI Operating Environment divides all system components, data inputs, and runtime contexts into five discrete **Trust Zones**. Trust does not flow transitively; every boundary crossing requires explicit schema validation, sanitization, and cryptographic or deterministic policy checks.

```
+===========================================================================+
| ZONE 0: TRUSTED COMPUTING BASE (TCB) & HOST OPERATING SYSTEM               |
| - Windows Kernel & OS APIs (UIA, Process APIs)                            |
| - Windows Data Protection API (DPAPI) Hardware Key Vault                   |
+=====================================+=====================================+
                                      | [Boundary 0-1]
+=====================================v=====================================+
| ZONE 1: TRUSTED HOST SECURITY CORE                                        |
| - Deterministic Policy Matrix & Rule Evaluator                            |
| - Action Validator & Canonical Path Confiner                              |
| - Human Approval Broker (CSPRNG Nonce Generator)                          |
| - Tamper-Evident SHA-256 Audit Logger                                     |
+==================+====================================+===================+
                   | [Boundary 1-2]                     | [Boundary 1-3]
+==================v==================+  +==============v===================+
| ZONE 2: MEDIATING ORCHESTRATION     |  | ZONE 3: TRUSTED USER BOUNDARY     |
| - FastAPI Task Planner & State Mach |  | - WinUI 3 Desktop Application     |
| - Provider Adapters (Outgoing Only) |  | - User Keystrokes & Click Approvals
| - Contextual Brain (Memory Store)   |  | - Encrypted Local Storage Cache   |
+==================+==================+  +==================================+
                   | [Boundary 2-4]
+==================v========================================================+
| ZONE 4: UNTRUSTED BOUNDARY (EXTERNAL / ADVERSARIAL)                       |
| - Model Completions & Generated JSON Proposals                            |
| - External Web Pages, Retrived Documents, Third-Party Repositories        |
| - Sandboxed Child Processes & Executing Scripts                           |
| - Remote Cloud AI Endpoints & Local Inference Outputs                     |
+===========================================================================+
```

---

## 2. Trust Zone Classifications

### Zone 0: Trusted Computing Base (TCB)
* **Components:** Windows Kernel, File System Driver, Windows UI Automation Provider, Windows DPAPI (`CryptProtectData`).
* **Trust Level:** Authoritative. This is the underlying hardware and OS foundation.
* **Access Rule:** Only Zone 1 components with host rights may invoke Zone 0 APIs directly.

### Zone 1: Trusted Host Security Core
* **Components:** Policy Engine, Action Schema Validator, Canonical Path Confiner, Approval Nonce Broker, Audit Logger.
* **Trust Level:** High Trust. Governs what operations are permissible.
* **Integrity Guard:** Cannot be modified, configured, or bypassed by Zone 2 or Zone 4 entities. All code in Zone 1 is deterministic, test-driven, and static.

### Zone 2: Mediating Orchestration Layer
* **Components:** Task Planner, Context Prioritizer, Memory Engine, Provider Adapter Registry.
* **Trust Level:** Medium / Mediating. Coordinates workflow execution.
* **Integrity Guard:** Does not have authority to execute OS commands or file writes directly. Must delegate all privileged actions to Zone 1.

### Zone 3: Trusted User Boundary
* **Components:** WinUI 3 Desktop Frontend, User Confirmation Inputs, Master Kill Switch.
* **Trust Level:** High Trust (Human Intent). Represents the authenticated human operator.
* **Integrity Guard:** Actions originating here (e.g. clicking "Approve") are cryptographically signed with session nonces before acceptance by Zone 1.

### Zone 4: Untrusted Entities & External Inputs
* **Components:** LLM Token Streams, AI Tool Proposals, Ingested Files/Websites, Subprocess Stdout/Stderr.
* **Trust Level:** **Zero Trust (Untrusted).**
* **Integrity Guard:** Any input from Zone 4 is treated as potentially malicious, tainted, or adversarial. It cannot trigger actions without full schema parsing and Zone 1 policy evaluation.

---

## 3. Boundary Crossing Protocols & Gateways

### Gateway B24: External Model Output -> Orchestration Engine
* **Protocol:** HTTPS / TLS 1.3 or Local Loopback Socket.
* **Direction:** Inbound from Zone 4 to Zone 2.
* **Enforced Verification:**
  1. **Strict Deserialization:** Raw model output is parsed strictly against Pydantic model schemas. Free text outside designated tool-call fields is discarded for execution purposes.
  2. **No Eval / No Dynamic Invocation:** Tool names are mapped via static dictionaries; no dynamic string evaluation (`eval()`, `getattr()`) is allowed.
  3. **Credential Scrubbing:** Inbound responses are filtered to ensure models do not reflect back secret tokens.

### Gateway B12: Orchestration Engine -> Security Core
* **Protocol:** In-Process Direct Interface or Authenticated Local IPC.
* **Direction:** Zone 2 requests action execution from Zone 1.
* **Enforced Verification:**
  1. **Canonicalization:** Filesystem targets are resolved using `os.path.realpath` to strip symlinks, directory traversal tokens (`..`), and alternate data streams (`::$DATA`).
  2. **Boundary Confinement Check:** Target paths must be prefixed with the active workspace directory. System paths (`C:\Windows`, `C:\Program Files`, `Users\<User>\AppData\Local\Microsoft`) are unconditionally blocked.
  3. **Policy Evaluation:** The requested tuple `(AgentID, ToolName, CanonicalPath, Parameters)` is matched against deterministic rules.
  4. **Decision Outcomes:**
     * `ALLOW`: Dispatches to Zone 0/Windows Integration for execution.
     * `DENY`: Rejection logged with reason; returns structured error to agent.
     * `REQUIRE_APPROVAL`: Triggers Gateway B13 to query user.

### Gateway B13: Security Core -> WinUI Client (Approval Protocol)
* **Protocol:** Full-Duplex Local WebSocket (`127.0.0.1`) with Session Token.
* **Direction:** Bi-directional between Zone 1 and Zone 3.
* **Enforced Verification:**
  1. **Nonce Generation:** Zone 1 generates a 256-bit CSPRNG nonce (`approval_nonce`) tied to the specific action hash.
  2. **Immutable Presentation:** The WinUI 3 dialog displays immutable action parameters: Target Path, Exact Command Array, Risk Tier, and Rationale.
  3. **User Signature Verification:** When user clicks "Approve", the client sends `(approval_nonce, action_hash, timestamp, user_decision)`. Zone 1 validates the nonce match and consumes it immediately (single-use).

### Gateway B10: Security Core -> Windows Integration (Execution Gateway)
* **Protocol:** Controlled Windows Subprocess / UIA API invocation.
* **Direction:** Zone 1 dispatches to Zone 0.
* **Enforced Verification:**
  1. **Subprocess Isolation:** Commands run via `subprocess.Popen` with `shell=False` and explicit argument lists.
  2. **Environment Sanitization:** System environment variables are stripped of credentials, session keys, and sensitive paths.
  3. **Job Object Containment:** Child processes are assigned to a Windows Job Object with memory limits (1GB default), CPU percentage throttling, and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
  4. **Output Buffering:** Output streams are bounded to 50KB / 2000 lines to prevent memory exhaustion DoS.

---

## 4. Taint Tracking and Propagation Rules

To defeat **Indirect Prompt Injection**, WinAI-OE implements explicit **Data Taint Tracking**:

1. **Taint Classification:**
   * `TAINT_CLEAN`: System configurations, user direct input, static schemas.
   * `TAINT_UNTRUSTED`: Files read from disk, web scraping results, tool stdout/stderr, git commit messages, clipboard content.
2. **Propagation Rule:**
   * When an agent processes context containing `TAINT_UNTRUSTED` inputs, the agent session state becomes tainted (`SESSION_TAINTED`).
3. **Policy Downgrade on Taint:**
   * Any tool action proposed by a `SESSION_TAINTED` agent that modifies files, runs commands, or sends network requests automatically drops from `ALLOW` to `REQUIRE_APPROVAL`.
   * Untrusted instructions cannot leverage previously cached ambient permissions.

---

## 5. Summary of Boundary Enforcement

| Crossing | Source Zone | Target Zone | Gatekeeper | Failure Action |
| :--- | :--- | :--- | :--- | :--- |
| Model Completion | 4 (Untrusted) | 2 (Orchestration) | Pydantic Schema Parser | Drop payload, return parse error |
| Action Proposal | 2 (Orchestration) | 1 (Security Core) | Policy Engine & Path Jail | Reject action, record security alert |
| User Approval | 3 (User Client) | 1 (Security Core) | CSPRNG Nonce Validator | Deny action, invalidate nonce |
| Command Execution | 1 (Security Core) | 0 (Windows OS) | Job Object & Env Sanitizer | Kill process tree, enforce timeout |
| Memory Ingestion | 4 (Untrusted) | 2 (Context Brain) | Taint Marker & Content Sanitizer | Tag as `TAINT_UNTRUSTED` |
