# REPOSITORY STRUCTURE SPECIFICATION
## Windows AI Operating Environment (WinAI-OE)
**Document Version:** 1.0.0  
**Phase:** Stage I — Phase 5 (Repository Structure)  
**Status:** Approved Specification  

---

## 1. Modular Architecture Overview

The repository is structured to physically isolate trust zones and functional concerns, preventing accidental cross-boundary coupling:

```
D:\Interveiewsass\
├── docs/                             # Architecture, Threat Model, Trust Boundaries, PRD
│   ├── PRD.md                        # Phase 1 Deliverable
│   ├── ARCHITECTURE.md               # Phase 2 Deliverable
│   ├── THREAT_MODEL.md               # Phase 3 Deliverable
│   ├── TRUST_BOUNDARIES.md           # Phase 4 Deliverable
│   └── REPO_STRUCTURE.md             # Phase 5 Deliverable
├── src/
│   ├── client/                       # Zone 3: Native WinUI 3 Desktop Frontend (C# / .NET 8)
│   │   └── WinAI.Client/
│   │       ├── Views/                # ChatView, ModelsView, WorkspaceView, ApprovalDialog
│   │       ├── ViewModels/           # ChatViewModel, ApprovalViewModel, WorkspaceViewModel
│   │       ├── Services/             # WebSocketClient, RestApiClient, DpapiVaultService
│   │       └── Models/               # Client-side state representations
│   ├── orchestrator/                 # Zone 2: AI Orchestration Subsystem (Python FastAPI)
│   │   ├── api/                      # REST and WebSocket endpoints
│   │   ├── planner/                  # Task decomposition, coordinator, execution loops
│   │   └── brain/                    # Working, Episodic, Semantic, and Project memory
│   ├── security/                     # Zone 1: Independent Security Core (Host-Side Policy Engine)
│   │   ├── policy_engine.py          # Deterministic Allow / Deny / RequireApproval rules
│   │   ├── action_validator.py       # Pydantic schema validation & path jail checks
│   │   ├── approval_broker.py        # CSPRNG nonces for human-in-the-loop approvals
│   │   ├── taint_tracker.py          # Untrusted context taint flags
│   │   └── audit_logger.py           # SHA-256 tamper-evident chained JSONL audit logs
│   ├── providers/                    # Zone 4 Boundary: Multi-Provider LLM Adapters
│   │   ├── base.py                   # BaseModelProvider abstract base class
│   │   ├── registry.py               # Dynamic adapter registration and lifecycle
│   │   ├── mock_provider.py          # Deterministic offline mock for testing
│   │   ├── openai_adapter.py         # OpenAI official integration
│   │   ├── anthropic_adapter.py      # Anthropic official integration
│   │   ├── gemini_adapter.py         # Google Gemini integration
│   │   └── local_adapter.py          # Local inference integration (Ollama / LM Studio)
│   ├── windows_integration/          # Zone 0/1 Boundary: Controlled OS Interaction
│   │   ├── process_runner.py         # Sandboxed subprocesses via Windows Job Objects
│   │   ├── file_service.py           # Canonicalized path confinement & atomic writes
│   │   └── uia_service.py            # Windows UI Automation accessible controls
│   └── storage/                      # Persistence Subsystem
│       ├── database.py               # SQLite WAL engine (PostgreSQL compatible)
│       ├── models.py                 # SQLAlchemy ORM models
│       └── credential_vault.py       # Windows DPAPI encryption wrapper
└── tests/
    ├── unit/                         # Fast isolated component unit tests
    ├── integration/                  # Cross-boundary API & WebSocket tests
    └── security/                     # Penetration, traversal & prompt injection tests
```

---

## 2. Package Boundaries & Import Rules

1. `src/security` must **NEVER** import from `src/orchestrator` or `src/providers`. The Security Core is completely autonomous.
2. `src/providers` must **NEVER** import from `src/windows_integration`. Providers cannot execute OS actions directly.
3. `src/orchestrator` communicates with `src/windows_integration` strictly through `src/security` mediation.
4. `src/client` is out-of-process and communicates solely over the local loopback IPC boundary (`http://127.0.0.1:8765` and `ws://127.0.0.1:8765/ws/v1/stream`).
