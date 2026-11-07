# SYSTEM ARCHITECTURE SPECIFICATION
## Sovereign Indian Voice Operating System (RAY / WinAI-OE)

**Document Version:** 3.0.0  
**Platform:** Windows 10 / 11 (x64) • Python 3.11 / 3.12 • FastAPI • WinMM & WebAudio  
**Speech Stack:** Gnani.ai Sovereign Speech APIs (`Gnani Prisma STT` & `Gnani Timbre TTS`)  
**Security Posture:** Zero Implicit Trust • Windows DPAPI Hardware Vault • SHA-256 Hash-Chained Audit Ledger  

---

## 1. Architectural Vision

Most AI assistants are trapped inside a browser tab and assume the user can type complex prompts in English. **RAY (WinAI-OE)** re-architects human-computer interaction from the ground up as a **Sovereign Indian Voice Operating System** for Windows 11:

1. **Zero-Typing Native Speech Interface:** Listens continuously in **Telugu (`te-IN`)**, **Hindi (`hi-IN`)**, **Tamil (`ta-IN`)**, **Kannada (`kn-IN`)**, **Malayalam (`ml-IN`)**, **Bengali (`bn-IN`)**, **Marathi (`mr-IN`)**, **Gujarati (`gu-IN`)**, **Punjabi (`pa-IN`)**, **Hinglish (`hi-en`)**, and **Indian English (`en-IN`)** via **Gnani Prisma STT** (`WSS /stt/v3/stream`).
2. **Sub-200ms Real-Time Barge-In & Atomic Recovery:** Users can interrupt an active drawing stroke or multi-step workflow mid-flight in their native language (*"ఆపు! Undo చెయ్"* / *"Ruko!"* / *"Nillu!"*), triggering immediate hardware input release and in-place plan correction.
3. **Deterministic Native OS Execution:** Operates real Windows applications (**Microsoft Paint**, **Microsoft Excel COM & BI Charts**, **Visual Studio Code**, **Notepad**, **Chrome**, and **Windows File Explorer**) using the Windows UI Automation (`IUIAutomation`) accessibility tree, Win32 HWND focus guards, and pixel-verified computer vision.
4. **Out-of-Band Zero-Trust Security Core:** Generative models have zero direct execution authority. Every proposed tool call traverses a deterministic host-side policy engine, Pydantic schema validator, and 256-bit CSPRNG human-in-the-loop approval broker.

---

## 2. End-to-End System Architecture (Mermaid)

```mermaid
graph TB
    classDef audio fill:#0284c7,stroke:#38bdf8,stroke-width:2px,color:#ffffff;
    classDef gnani fill:#6d28d9,stroke:#a78bfa,stroke-width:2px,color:#ffffff;
    classDef brain fill:#0f766e,stroke:#2dd4bf,stroke-width:2px,color:#ffffff;
    classDef sec fill:#b91c1c,stroke:#f87171,stroke-width:2px,color:#ffffff;
    classDef os fill:#1e293b,stroke:#94a3b8,stroke-width:2px,color:#ffffff;

    subgraph UserLayer["1. Multimodal Presentation & Audio Capture Layer"]
        Mic["Windows WinMM / WebAudio PCM16 Capture<br/>16 kHz Mono • 1,024-byte Frames"]:::audio
        Cockpit["Real-Time 3D Web Cockpit<br/>http://127.0.0.1:8765"]:::audio
        BgListener["Always-On Hands-Free Listener<br/>scripts/rahul_background_listener.py"]:::audio
        Speaker["Windows Audio Playback<br/>24 kHz WAV • Echo-Suppressed"]:::audio
    end

    subgraph GnaniCloud["2. Gnani.ai Sovereign Speech Cloud"]
        PrismaWS["Gnani Prisma STT WebSocket<br/>wss://api.vachana.ai/stt/v3/stream<br/>Sub-200ms VAD • Noise Cancellation"]:::gnani
        PrismaREST["Gnani Prisma STT REST<br/>POST /stt/v3 • ITN & Bias List"]:::gnani
        TimbreSSE["Gnani Timbre v3.0 & v2.5 TTS<br/>POST /api/v1/tts/sse & /inference<br/>Context-Aware Indic Prosody"]:::gnani
    end

    subgraph VoiceCore["3. Voice Pipeline & Conversational Intelligence"]
        WSProxy["Full-Duplex Voice Stream Proxy<br/>api_routes.py • Segment Stitching"]:::brain
        StopFastPath["Sub-200ms Stop / Undo Fast-Path<br/>CancelToken + Emergency Input Release"]:::brain
        ConvPlanner["Multilingual Conversational Planner<br/>conversational_planner.py<br/>Script & Language Verification"]:::brain
        TTSCache["Deterministic SHA-256 TTS Cache<br/>Sub-1ms Disk Replay"]:::brain
    end

    subgraph SecurityCore["4. Host-Side Zero-Trust Security Core"]
        Validator["Action Schema & Path Validator<br/>action_validator.py"]:::sec
        Policy["Deterministic Policy Engine<br/>ALLOW • DENY • REQUIRE_APPROVAL"]:::sec
        Broker["256-Bit CSPRNG Approval Broker<br/>Single-Use Nonce • 300s TTL"]:::sec
        Audit["Tamper-Evident SHA-256 Audit Ledger<br/>Append-Only JSONL"]:::sec
    end

    subgraph ExecutionLayer["5. Windows 11 Native OS Execution Engine"]
        PaintCtrl["MS Paint Scene & UIA Controller<br/>paint_controller.py • HWND Focus Guard"]:::os
        ExcelCtrl["Excel Grocery BI & COM Controller<br/>excel_grocery_controller.py"]:::os
        ShowpieceCtrl["Cross-App Workflow Controller<br/>VS Code Export • Downloads Summary • Timers"]:::os
        ScopedFS["Scoped Filesystem & Process Runner<br/>Atomic Pre-Write Backups • Job Objects"]:::os
    end

    subgraph StorageLayer["6. Hardware Security & Persistence"]
        DPAPI["Windows DPAPI Hardware Credential Vault<br/>CryptProtectData • Zero Plaintext Keys"]:::sec
        Memory["4-Tier Contextual Brain & SQLite WAL<br/>Working • Episodic • Semantic • Error Memory"]:::brain
    end

    Mic --> BgListener
    Mic --> Cockpit
    BgListener --> PrismaWS
    Cockpit --> WSProxy
    WSProxy <--> PrismaWS
    WSProxy --> PrismaREST
    PrismaWS --> StopFastPath
    PrismaWS --> ConvPlanner
    ConvPlanner --> Validator
    Validator --> Policy
    Policy -->|REQUIRE_APPROVAL| Broker
    Broker <--> Cockpit
    Policy -->|ALLOW| PaintCtrl
    Policy -->|ALLOW| ExcelCtrl
    Policy -->|ALLOW| ShowpieceCtrl
    Policy -->|ALLOW| ScopedFS
    PaintCtrl --> Audit
    ExcelCtrl --> Audit
    ShowpieceCtrl --> Audit
    ConvPlanner --> TTSCache
    TTSCache -->|Cache Miss| TimbreSSE
    TTSCache -->|WAV Stream| Speaker
    TimbreSSE --> Speaker
    DPAPI -.->|In-Memory Key Hydration| PrismaWS
    DPAPI -.->|In-Memory Key Hydration| TimbreSSE
    Memory <--> ConvPlanner
```

---

## 3. Real-Time Streaming, Barge-In & Atomic Undo Sequence

A core engineering requirement of a voice operating system is **instant interruptibility**. When the user speaks a stop or correction phrase mid-execution (*"ఆపు! Undo చెయ్. Gold colour వాడు"* / *"Change the color of the circle to yellow"*), the system halts physical mouse/keyboard actions in under `200ms`, releases all OS input locks, and mutates the active `SceneSpec` in place.

```mermaid
sequenceDiagram
    autonumber
    actor User as User (Telugu / Hindi / Tamil / Hinglish)
    participant Mic as WinMM / AudioWorklet (16kHz PCM16)
    participant Prisma as Gnani Prisma STT (WSS /stt/v3/stream)
    participant Router as Voice Pipeline Router (api_routes.py)
    participant Paint as Windows11PaintController (UIA + Win32)
    participant Timbre as Gnani Timbre v3.0 TTS (SSE + SHA-256 Cache)

    User->>Mic: "రాహుల్, పెయింట్ లో సర్కిల్ విత్ కలర్ రెడ్"
    Mic->>Prisma: Stream 1,024-byte PCM16 chunks (32ms cadence)
    Prisma-->>Router: TranscriptEvent (is_final=True, lang=te-IN)
    Router->>Paint: execute_scene_spec(SceneSpec[ellipse, fill=red])
    activate Paint
    Paint->>Paint: Verify HWND Focus Guard + Inspect UIA Ribbon
    Paint->>Paint: Draw Ellipse + Flood-Fill Red (RGB Verified)
    Paint-->>Router: COMPLETED (strokes_completed=1)
    deactivate Paint
    Router->>Timbre: Synthesize("పెయింట్ లో ellipse గీసి దానికి red రంగు వేశాను!")
    Timbre-->>User: Play 24kHz WAV Audio Reply

    Note over User,Paint: Mid-Workflow Correction and Sub-200ms Barge-In
    User->>Mic: "చేంజ్ ద కలర్ ఆఫ్ సర్కిల్ టు ఎల్లో!"
    Mic->>Prisma: Stream PCM16 chunks (min_silence_ms=280)
    Prisma-->>Router: TranscriptEvent ("చేంజ్ ద కలర్ ఆఫ్ సర్కిల్ టు ఎల్లో")
    Router->>Paint: apply_scene_edit(SceneEditSpec[recolour, target=ellipse, color=yellow])
    activate Paint
    Paint->>Paint: Ctrl+Z Undo Last Object + Redraw Ellipse with Yellow Fill
    Paint-->>Router: COMPLETED (RGB Pixel Verified)
    deactivate Paint
    Router->>Timbre: Synthesize("పెయింట్ లో ellipse గీసి దానికి yellow రంగు వేశాను!")
    Timbre-->>User: Play Spoken Confirmation
```

---

## 4. Conversational Planner & Multilingual Script Verification

Located in `src/voice/conversational_planner.py`, the planner enforces strict language and script alignment across 5 primary reply locales (`te-IN`, `hi-IN`, `hi-en`, `ta-IN`, `en-IN`) with bounded 3-turn memory (`240` tokens max):

```mermaid
flowchart LR
    A["Raw Transcript<br/>Prisma STT"] --> B["Wake-Word Stripper<br/>strip_rahul_wake_word"]
    B --> C{"Instant Wake<br/>Greeting?"}
    C -->|Yes Sub-1ms| D["Pre-Warmed Greeting Cache<br/>te-IN / hi-IN / ta-IN / en-IN"]
    C -->|No| E{"Deterministic OS<br/>Domain Match?"}
    E -->|Excel BI / Grocery| F["ExcelGroceryController<br/>25-Row Ground Truth + COM Charts"]
    E -->|MS Paint Scene / Edit| G["Windows11PaintController<br/>SceneSpec / SceneEditSpec"]
    E -->|VS Code / Reports / Web| H["SutraShowpieceController<br/>Code Export / Downloads Summary"]
    E -->|Open Conversation| I["ConversationalTurnPlanner<br/>2-Sentence Cap + Digit Normalization"]
    I --> J{"Script Ratio<br/>Over 50 Percent?"}
    J -->|Pass| K["Gnani Timbre v3.0 SSE Synthesis"]
    J -->|Retry / Fallback| L["Localized System Fallback"]
    D --> K
    F --> K
    G --> K
    H --> K
    L --> K
```

---

## 5. Multi-Application Execution State Machine

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> LISTENING: Wake Word / Auto-VAD Speech Detected
    LISTENING --> TRANSCRIBING: Prisma Server VAD Silence (280ms)
    TRANSCRIBING --> STOPPED: Stop / Undo Fast-Path (Sub-200ms)
    TRANSCRIBING --> UNDERSTANDING: Final Transcript Stitched
    UNDERSTANDING --> AWAITING_APPROVAL: Policy Engine = REQUIRE_APPROVAL
    UNDERSTANDING --> EXECUTING: Policy Engine = ALLOW
    AWAITING_APPROVAL --> EXECUTING: Cryptographic Nonce Approved
    AWAITING_APPROVAL --> CANCELLED: Denied / 300s TTL Expired
    EXECUTING --> ABORTED_SAFE: HWND Focus Shift / Escape Key Guard
    EXECUTING --> SPEAKING: OS Action Verified (UIA / Pixel / File Hash)
    SPEAKING --> LISTENING: Speaker Playback Complete (Mic Re-Armed)
    STOPPED --> LISTENING: Emergency Input Release Complete
    ABORTED_SAFE --> LISTENING: Safe Cleanup Complete
```

---

## 6. Subsystem Specifications

### 6.1 Gnani.ai Sovereign Speech Integration (`src/voice/`)
- **Speech-to-Text (`gnani_stt.py`):**
  - **Realtime WebSocket (`wss://api.vachana.ai/stt/v3/stream`):** Streams 16-bit 16 kHz mono PCM frames (`1,024` bytes = `32ms` per frame) with configurable server VAD headers (`x-vad-threshold`, `x-min-silence-ms`, `x-min-speech-ms`, `x-min-segment-ms`), keyword biasing (`x-bias-list`, `x-bias-score`), and dynamic runtime reconfiguration via `update_settings` frames.
  - **REST Batch / File Decode (`POST https://api.vachana.ai/stt/v3`):** Supports multipart audio uploads with noise cancellation (`nc=true`), Inverse Text Normalization (`format=transcribe` for `hi-IN` and `en-IN`), and circuit-breaker resilience.
- **Text-to-Speech (`gnani_tts.py`):**
  - **SSE Streaming (`POST https://api.vachana.ai/api/v1/tts/sse`) & REST (`POST /api/v1/tts/inference`):** Strict catalog validation across **`timbre-v3.0`** (13 multilingual voices speaking 10 Indic languages) and **`timbre-v2.5`** (42 voices).
  - **RIFF Header Repair & SHA-256 Disk Cache:** Automatically repairs `0xFFFFFFFF` streaming WAV header sentinels and caches synthesized utterances by `sha256(text + voice + model)` in `.winai/tts_cache/` for `<1ms` repeat playback.

### 6.2 Windows 11 Native OS Controllers (`src/windows_integration/`)
- **`Windows11PaintController` (`paint_controller.py`):**
  - Discovers MS Paint canvas bounds, tools, shapes, and colour swatches dynamically via the Windows UI Automation (`IUIAutomation`) tree with zero brittle hardcoded coordinates.
  - Enforces a per-stroke **HWND & PID Foreground Guard** (`verify_foreground_guard`) and registers an `atexit` / signal **Emergency Input Release** handler that unconditionally releases mouse buttons and modifier keys (`LBUTTON`, `CTRL`, `SHIFT`, `ALT`).
  - Supports structured `SceneSpec` drawing (including a 20-object Starry Night Futuristic City skyline) and in-place `SceneEditSpec` mutations (`recolour`, `resize`, `move`, `add`, `remove`).
- **`ExcelGroceryController` (`excel_grocery_controller.py`):**
  - Manages a 25-item Indian grocery billing dataset with exact Python ground-truth analytics (Subtotal, item-wise GST, UPI vs. Card vs. Cash breakdown, category totals, and low-stock alerts).
  - Launches Microsoft Excel (`XLMAIN`) via COM automation to render native Excel bar and pie charts live on screen and answers multilingual voice queries in `<10ms`.
- **`SutraShowpieceWorkflowController` (`sutra_showpiece_workflows.py`):**
  - **Cross-App Design-to-Code:** Exports active MS Paint `SceneSpec` scenes into responsive HTML5/SVG + JSON blueprints and opens them directly in **Visual Studio Code**.
  - **Autonomous Local Report Summarizer:** Scans `~/Downloads` for the latest `.pdf` / `.md` / `.txt` report, extracts key findings into a bilingual executive summary, and opens it in **Notepad**.
  - **Ambient Audio & OS Reminders:** Synthesizes warm harmonic ambient audio streams and persists verified desktop reminders (`workspace/reminders/active_reminders.json`).

### 6.3 Security, Privacy & Credential Hygiene (`src/security/`, `src/storage/`)
- **Windows DPAPI Hardware Credential Vault (`credential_vault.py`):** Encrypts all API keys using `CryptProtectData` bound to the local Windows user login and TPM. **Zero API keys or secrets are ever stored in plaintext or committed to Git.**
- **Sanitized Error & Log Redaction (`audit_logger.py`, `base.py`):** Automatically scrubs headers, bearer tokens, and `x-api-key-id` values from all exceptions, logs, and UI responses.
- **Tamper-Evident Audit Chain:** Every tool execution and policy decision is appended to a SHA-256 hash-chained JSONL ledger.
