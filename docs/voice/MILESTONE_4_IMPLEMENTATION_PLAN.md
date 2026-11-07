# Milestone 4 Implementation Plan — WinAI-OE Command Cockpit, 3D Core & Real Voice Integration

**Status:** Active Implementation Plan (Phase Two Audit & Architecture Plan)

---

## 1. Repository & UI Architecture Audit

### 1.1 Discovered Surfaces in the Repository
1. **WinUI 3 C# Scaffold (`src/client/WinAI.Client/`):**
   - Contains 16 MVVM/XAML files (`MainWindow.xaml`, `ChatView.xaml`, `ApprovalDialog.xaml`, `IpcService.cs`, `ViewModels/*.cs`, `Models/*.cs`) verified by XML/file-existence tests in `tests/unit/test_client_architecture.py`.
   - However, `src/client/WinAI.Client` has no `App.xaml` or `Program.cs` entry point, and the local Windows machine has no `.NET SDK` installed (`dotnet --list-sdks` is empty).
2. **Primary Runnable & Packaged Surface (`src/orchestrator/main.py` + `src/orchestrator/dashboard.html`):**
   - `run_vertical_slice.py` and `archive/experiments/winai_service_entry.py` (compiled into `WinAI-OE.exe` via PyInstaller `--add-data 'src/orchestrator/dashboard.html;src/orchestrator'`) serve the primary UI from `src/orchestrator/dashboard.html` on strict loopback (`http://127.0.0.1:8765/` and `/dashboard`).
   - Existing unit tests (`tests/unit/test_app_scanner.py` and `tests/unit/test_jev_ui.py`) assert that `GET /dashboard` includes `"Enterprise Autonomous AI Employee"`, `"Operating Environment"`, `"jevCard"`, `"MOCK MODE"`, and `"/api/v1/jev/status"`.
3. **Tkinter Approval Badge (`approval_button.py` & `src/windows_integration/approval_overlay.py`):**
   - Secondary floating badge polling `/api/v1/approval/pending`.

### 1.2 Chosen Primary UI Architecture & Rationale
We select the **WinAI-OE Living Intelligence Command Cockpit (`src/orchestrator/dashboard.html` served at `/`, `/dashboard`, and `/cockpit`, paired with a Native Windows App-Window Launcher `winai_cockpit.py` and synchronized `WinAI.Client` XAML/ViewModels)** as the single unified primary user experience:
1. **Why this surface is necessary and appropriate:**
   - It is the actual UI bundled into `WinAI-OE.exe` by PyInstaller (`build-exe.yml`) and tested by the FastAPI `TestClient` suite.
   - Using an embedded hardware-accelerated HTML5 Canvas / 3D renderer inside the native desktop app window (`msedge/chrome --app=http://127.0.0.1:8765/cockpit` or any loopback window) delivers smooth 60fps 3D volumetric rendering, native Web Audio / WinMM RMS visualization, instant zero-build execution, and 100% compatibility with PyInstaller packaging.
   - We also update `src/client/WinAI.Client/Views/MainWindow.xaml` and `ViewModels/MainViewModel.cs` so the C# WinUI 3 shell definition reflects the same 10-state Command Cockpit contract while keeping `tests/unit/test_client_architecture.py` green.
2. **How communication is secured & credentials are protected:**
   - Bound strictly to `127.0.0.1` (`AppConfig.validate_host_loopback` forbids `0.0.0.0`).
   - All API keys (`gnani`, `gemini`, etc.) remain sealed in the Windows DPAPI `CredentialVault` (`~/.winai/vault.enc`) and `GnaniConfig(SecretStr)`. Zero secrets or headers are ever placed in HTML, JS, or JSON responses.
   - All consequential actions pass through `VoiceExecutionCoordinator` → `SecurityPolicyEngine` → `ApprovalBroker` → `WindowsExecutionEngine`. Frontend UI state can never self-authorize an action.

---

## 2. Backend Integration Map (`src/voice/api_routes.py` + `src/orchestrator/main.py`)

We will create `src/voice/api_routes.py` (`create_voice_router(...)`) and mount it in `src/orchestrator/main.py` using the **exact same authoritative `policy_engine` and `dispatcher` instances**:

| Endpoint | Method | Purpose & Security Contract |
|---|---|---|
| `/api/v1/voice/capabilities` | `GET` | Returns truthful `CapabilityRegistry` report (`stt`, `tts`), configured reasoning provider status (`ProviderRegistry`), enumerated WinMM input/output audio devices (`SpeechInputManager.list_devices()`, `SpeechOutputManager.list_devices()`), and active voice settings. Performs network connectivity verification only when `?verify=true` is explicitly passed. |
| `/api/v1/voice/session/start` | `POST` | Creates and activates a new `VoiceIOSession`. Only starts microphone capture when `start_capture: true` is explicitly requested by user action. |
| `/api/v1/voice/session/{session_id}/status` | `GET` | Returns real-time session state (`IDLE`, `LISTENING`, `TRANSCRIBING`, `UNDERSTANDING`, `AWAITING_APPROVAL`, `EXECUTING`, `SPEAKING`, `SUCCESS`, `ERROR`, `CANCELLED`), measured microphone RMS (`0.0–1.0`), captured bytes, and active task count. |
| `/api/v1/voice/session/{session_id}/stop` | `POST` | Stops microphone capture on `session_id` and, if captured frames or transcript are present, runs `VoiceExecutionCoordinator`. |
| `/api/v1/voice/session/{session_id}/cancel` | `POST` | Invokes `await session.cancel()`, revokes all pending approvals for `session_id` in `ApprovalBroker`, and returns `CANCELLED` only after hardware/task cleanup completes. |
| `/api/v1/voice/turn` | `POST` | Processes an explicit voice turn (either via live microphone session or spoken/typed transcript) through `VoiceExecutionCoordinator.process_transcript()`, returning `VoicePipelineResult` (`COMPLETED`, `WAITING_FOR_APPROVAL`, `DENIED_BY_POLICY`, `REJECTED_VALIDATION`, `FAILED_EXECUTION`, `CANCELLED`). |
| `/api/v1/voice/approval/resolve` | `POST` | Resolves a pending `approval_nonce` (`approved: bool`) through `VoiceExecutionCoordinator.resolve_approval()`, enforcing session binding, SHA-256 `action_hash` argument binding, TTL check, and pre-execution policy revalidation. |
| `/api/v1/voice/activity` | `GET` | Returns sanitized recent voice turns, pending approvals, and recent `AuditLogger` events (redacted via `redact_secrets()`). |
| `/api/v1/voice/settings` | `POST` | Updates non-secret voice preferences (`input_device_id`, `output_device_id`, `language_code`, `voice`, `speak_responses`, `theme`, `motion_mode`). |

---

## 3. UI Component & 3D Rendering Plan (`src/orchestrator/dashboard.html`)

### 3.1 Main Command Cockpit Layout
1. **Top Telemetry & Identity Bar:**
   - Product identity (`WinAI-OE · Living Intelligence Interface` + subtitle preserving `"Enterprise Autonomous AI Employee"` / `"Operating Environment"` for test compatibility).
   - Live Capability Pills: `STT: Prisma v2.5`, `TTS: Timbre v3.0`, `Planner: Gemini / Deterministic`, `Policy: Strict DPAPI`.
   - Navigation switcher: `Command Cockpit` (default primary view), `Activity & Audit Ledger`, `Applications & JEV`, `Settings & Devices`, plus quick Theme toggle (`Dark` / `Light`) and prominent **`EMERGENCY STOP`** button.
2. **Centerpiece: The 3D Harmonic Tensor Core (`<canvas id="intelligenceCoreCanvas">`):**
   - Custom 3D perspective renderer with 42-vertex geodesic icosahedron, 2 counter-rotating 3D orbital rings, and 64-point radial acoustic waveform ring.
   - Driven by a real state machine with 10 distinct states: `IDLE`, `LISTENING`, `TRANSCRIBING`, `UNDERSTANDING`, `AWAITING_APPROVAL`, `EXECUTING`, `SPEAKING`, `SUCCESS`, `ERROR`, `CANCELLED`.
   - Reacts to real microphone RMS amplitude during `LISTENING` and playback state during `SPEAKING`.
   - Automatically pauses rendering when `document.hidden` is true and flattens motion when `prefers-reduced-motion: reduce` or `motion_mode == "reduced"` is active.
3. **Persistent Voice Command Dock:**
   - **Push-to-Talk (`Hold Space`)**, **Start Listening**, **Stop Listening**, and **Cancel Session (`Esc`)** buttons.
   - Live Transcript & Validated Intent inspector bar + accessible text fallback input so users can always interact even when microphone/TTS hardware is unavailable.
4. **Authoritative Approval Card (`#voiceApprovalCard`):**
   - Appears only when `status == "WAITING_FOR_APPROVAL"` (never shown for policy-denied actions).
   - Displays: Requested Action, Exact Target Resource, Validated Arguments JSON, Expected Consequences, Policy Reason, and Expiration Countdown, with **Approve Once (`Enter`)**, **Deny**, and **Cancel Session (`Esc`)** buttons wired to `/api/v1/voice/approval/resolve`.
5. **Compact Activity & Verification Trace Region:**
   - Displays real execution turns with distinct badges for **Action Execution Outcome** vs. **TTS Playback Outcome** (so a TTS failure on a completed task clearly shows `Action: COMPLETED · TTS: Text Fallback`).

---

## 4. Testing & Packaging Plan

1. **Automated UI, Integration & Security Tests (`tests/unit/test_voice_milestone4.py`):**
   - Verify all 10 visual states and required cockpit controls are present and wired in the served UI.
   - Test full API lifecycle: `/api/v1/voice/capabilities`, `/api/v1/voice/session/start`, `/api/v1/voice/turn` (allowlisted action, policy denial, approval required -> approve/deny, TTS failure after successful action), `/api/v1/voice/session/{id}/cancel`, `/api/v1/voice/activity`, and `/api/v1/voice/settings`.
   - Prove that frontend payload tampering cannot bypass `SecurityPolicyEngine` or `ApprovalBroker`.
2. **Packaged Executable Verification:**
   - Run `pyinstaller` to build `WinAI-OE.exe` (or verify existing packaged build flow), start the packaged process on a test loopback port, and verify `/health`, `/dashboard`, and `/api/v1/voice/capabilities`.
