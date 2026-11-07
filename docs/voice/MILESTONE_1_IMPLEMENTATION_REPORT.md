# Milestone 1 Implementation Report — Gnani Prisma STT, Timbre TTS & Capability Registry

**Status:** Complete  
**Milestone Scope:** `GnaniSTTProvider`, `GnaniTTSProvider`, `CapabilityRegistry`, shared voice contracts/RIFF utilities, and deterministic offline + opt-in live tests.

---

## 1. Status

**Complete.** All Milestone 1 requirements, Definition-of-Done items, security controls, deterministic unit tests, opt-in live API verification, Milestone 0 regression suites, dependency drift checks, diagnostics checks, and `ruff` lint checks passed with zero errors.

---

## 2. Architecture Discovered & Reused

1. **Error Hierarchy (`src/orchestrator/errors.py`):**
   - Reused `ProviderError(WinAIError)` as the base class for `VoiceProviderError` and its subclasses (`VoiceConfigError`, `VoiceAuthError`, `VoiceRateLimitError`, `VoiceTimeoutError`, `VoiceInvalidAudioError`, `VoiceConnectionError`), preserving the `.to_dict()` contract (`error_code`, `message`, `remediation`).
2. **Resilience Primitives (`src/providers/resilience.py`):**
   - Reused `CircuitBreaker` (`failure_threshold=5`, `recovery_timeout=30.0`) inside both `GnaniSTTProvider` and `GnaniTTSProvider`.
3. **Hardware Credential Vault (`src/storage/credential_vault.py`):**
   - Reused `CredentialVault` (`get_credential("gnani")`) inside `GnaniConfig.from_env_or_vault()` alongside `GNANI_API_KEY` and `VACHANA_API_KEY` environment resolution.
4. **Secret Redaction (`src/security/audit_logger.py`):**
   - Reused and extended `redact_secrets()` to scrub `vach_...` tokens, `X-API-Key-ID`, `x-api-key`, `Authorization`, and `Proxy-Authorization` across strings, dicts, lists, and tuples.
5. **Preservation of Existing LLM `ProviderRegistry` (`src/providers/registry.py`):**
   - `ProviderRegistry` is strictly typed to `BaseModelProvider` (verified by `test_phase_0024_registry_hardening.py` and `test_phase_0030_adapter_signoff.py`). Placing voice providers and `CapabilityRegistry` in `src/voice/` keeps `src/providers/registry.py` untouched.

---

## 3. Files Created & Modified

### Created Files
- `D:\Interveiewsass\Interveiewsass\docs\voice\MILESTONE_1_IMPLEMENTATION_PLAN.md` — Pre-implementation architecture and protocol plan.
- `D:\Interveiewsass\Interveiewsass\src\voice\__init__.py` — Package exports for Milestone 1 voice primitives.
- `D:\Interveiewsass\Interveiewsass\src\voice\base.py` — `GnaniConfig`, `BaseSTTProvider`, `BaseTTSProvider`, `STTTranscriptEvent`, `STTResult`, `TTSRequest`, `TTSAudioConfig`, `TTSAudioResult`, `TTSStreamChunk`, `WavHeaderMetadata`, `parse_wav_container()`, `normalize_wav_bytes()`, `build_wav_from_pcm()`, and sanitized `VoiceProviderError` subclasses.
- `D:\Interveiewsass\Interveiewsass\src\voice\gnani_stt.py` — `GnaniSTTProvider` (Prisma v2.5 REST `POST /stt/v3` and WebSocket `WSS /stt/v3/stream`).
- `D:\Interveiewsass\Interveiewsass\src\voice\gnani_tts.py` — `GnaniTTSProvider` (Timbre v2.5 & v3.0 REST `POST /api/v1/tts/inference` and SSE `POST /api/v1/tts/sse`).
- `D:\Interveiewsass\Interveiewsass\src\voice\capability_registry.py` — `CapabilityRegistry`, `CapabilityStatus`, and `ServiceCapabilityReport`.
- `D:\Interveiewsass\Interveiewsass\tests\unit\test_voice_milestone1.py` — 18 deterministic unit/opt-in live tests covering STT, TTS, WAV sentinel normalization, CapabilityRegistry, and credential redaction.
- `D:\Interveiewsass\Interveiewsass\docs\voice\MILESTONE_1_IMPLEMENTATION_REPORT.md` — This completion report.

### Modified Files
- `D:\Interveiewsass\Interveiewsass\src\security\audit_logger.py` — Added case-insensitive header-value redaction (`x-api-key-id`, `x-api-key`, `api-key`, `authorization`, `proxy-authorization`) and tuple traversal in `redact_secrets()`.
- `D:\Interveiewsass\Interveiewsass\tests\unit\test_audit_logger.py` — Added assertions verifying `Authorization`, `Proxy-Authorization`, `X-API-Key-ID`, and `x-api-key` redaction.

---

## 4. Prisma STT Verification

- **REST Endpoint (`POST https://api.vachana.ai/stt/v3`):**
  - Authenticated via `X-API-Key-ID`.
  - Supports all 10 documented BCP-47 locales (`bn-IN`, `en-IN`, `gu-IN`, `hi-IN`, `kn-IN`, `ml-IN`, `mr-IN`, `pa-IN`, `ta-IN`, `te-IN`), `format` (`verbatim` | `transcribe`), `itn_native_numerals`, `enable_substitution` + `substitution_map`, `bias_list` + `bias_score`, and `multi_lang_codes` (returning a dictionary of transcripts per locale).
- **Realtime WebSocket (`WSS wss://api.vachana.ai/stt/v3/stream`):**
  - Authenticated via `x-api-key-id` upgrade header.
  - Streams 1,024-byte frames (512 samples of 16-bit mono little-endian PCM).
  - Handles both `"transcript"` (emitted when `x-partials` is `false`) and `"final_transcript"` (emitted when `x-partials` is `true`), distinguishes `"partial_transcript"` (`stable_text` + `volatile_text`, `is_final=False`) from final events, deduplicates repeated final events per `segment_id` / `utterance_id`, ignores unknown event types without fabricating transcripts, and guarantees WebSocket and sender-task cleanup in `finally`.

---

## 5. Timbre TTS Verification & WAV Header Handling

- **Endpoints (`POST https://api.v1/tts/inference` and `POST https://api.vachana.ai/api/v1/tts/sse`):**
  - Authenticated via `X-API-Key-ID`.
  - Validates model (`timbre-v2.5` vs `timbre-v3.0`) against its respective voice catalog (`TIMBRE_V25_VOICES` vs `TIMBRE_V30_VOICES`) before issuing network requests.
- **RIFF WAVE Chunk-Walking Parser & `0xFFFFFFFF` Sentinel Repair:**
  - `parse_wav_container()` walks RIFF subchunks (`fmt `, `JUNK`, `LIST`, `fact`, `data`, etc.) rather than assuming a fixed 44-byte offset.
  - When `container="wav"` responses carry the streaming sentinel `0xFFFFFFFF` in the RIFF size or `data` subchunk size, `normalize_wav_bytes()` repairs both 32-bit little-endian size fields to match the exact buffered byte lengths (`wav_header_repaired=True`) and computes accurate `duration_seconds`.
  - When `container="raw"` is requested, raw PCM bytes are returned with `container="raw"`, `mime_type="application/octet-stream"` and never mislabeled as WAV.

---

## 6. CapabilityRegistry Verification

- Independently tracks `stt` and `tts` across 5 truthful states:
  1. `UNAVAILABLE`: provider not registered or failed connectivity check.
  2. `UNCONFIGURED`: provider registered but missing API key.
  3. `CONFIGURED`: API key present locally, connectivity not yet verified.
  4. `REACHABLE`: non-destructive `GET /stt/v3/batch/jobs?limit=1` check succeeded.
  5. `OPERATIONALLY_TESTED`: a real STT or TTS operation completed and was verified.
- Makes zero network calls on initialization, `get_stt_report()`, `get_tts_report()`, or `to_dict()`.

---

## 7. Security Review

- `GnaniConfig.api_key` uses `pydantic.SecretStr` with `repr=False`.
- All `VoiceProviderError` subclasses and `CapabilityRegistry` reasons pass through `sanitize_error_message()` / `redact_secrets()`, scrubbing both `vach_...` patterns and any explicit secret value.
- `RestrictedProcessRunner` scrubs `GNANI_API_KEY`, `VACHANA_API_KEY`, and any `*api_key*` environment variables from subprocesses.

---

## 8. Test Results

| Suite / Command | Result | Exit Code |
|---|---|---|
| `python -m pytest tests/unit/test_voice_milestone1.py tests/unit/test_audit_logger.py -v` | `20 passed, 1 skipped` (1.98s) | `0` |
| `$env:WINAI_RUN_LIVE_GNANI_TESTS="1"; python -m pytest tests/unit/test_voice_milestone1.py -k "test_live_gnani_opt_in_roundtrip" -v` | `1 passed, 17 deselected` (8.62s) | `0` |
| `python -m pytest tests/unit tests/security tests/integration/test_vertical_slice.py tests/integration/test_control_plane.py tests/integration/test_windows_integration_stage7.py -k "not uia_accessible_tree" -q` | `443 passed, 1 skipped` (20.1s) | `0` |
| `python scripts/check_dependency_drift.py` | `Dependencies in sync between pyproject.toml and requirements.txt.` | `0` |
| `python scripts/diagnostics.py` | `All diagnostics checks passed.` | `0` |
| `python -m ruff check src/voice src/security/audit_logger.py tests/unit/test_voice_milestone1.py tests/unit/test_audit_logger.py` | `All checks passed!` | `0` |

---

## 9. Known Limitations & Milestone Boundary Confirmation

- **Known Limitations (By Design for Milestone 1):**
  - Microphone capture, speaker playback, voice session state machine, orchestration wiring, and UI overlays are intentionally excluded from Milestone 1 and deferred to Milestones 2–4.
  - Non-destructive reachability probing uses `GET /stt/v3/batch/jobs?limit=1` on `https://api.vachana.ai`, which verifies Vachana API gateway reachability and key authentication for the account without consuming TTS/STT inference credits.
- **Milestone Boundary Confirmation:**
  - Zero changes were made to any UI file, task dispatcher/orchestration flow, security policy engine, approval workflow, or filesystem confinement rule in Milestone 1.
