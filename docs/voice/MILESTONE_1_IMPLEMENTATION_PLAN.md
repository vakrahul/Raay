# Milestone 1 Implementation Plan — Gnani Prisma STT, Timbre TTS & Capability Registry

**Status:** Active Implementation Plan (Milestone 1 Only)  
**Scope:** `GnaniSTTProvider`, `GnaniTTSProvider`, `CapabilityRegistry`, shared voice contracts/audio utilities, and deterministic offline + opt-in live tests.

---

## 1. Existing Architecture Discovered

1. **Provider Layer (`src/providers/`):**
   - `src/providers/base.py` defines `BaseModelProvider` (`complete`, `stream`, `get_capabilities`, `health_check`) for text/chat LLM completion adapters (`GeminiAdapter`, `OpenAIAdapter`, `AnthropicAdapter`, `LocalModelAdapter`, `MockProvider`).
   - `src/providers/registry.py` (`ProviderRegistry`) strictly enforces `isinstance(provider, BaseModelProvider)` (verified by `tests/unit/test_phase_0024_registry_hardening.py` and `test_phase_0030_adapter_signoff.py`). Therefore, speech adapters (`STT` and `TTS`) and the voice `CapabilityRegistry` must live in `src/voice/` without altering `ProviderRegistry`'s 5-provider LLM surface.
   - `src/providers/resilience.py` provides `CircuitBreaker`, `CircuitBreakerOpenError`, `CircuitState`, `RETRYABLE_HTTP_STATUSES`, and `retry_with_backoff`, which can be reused directly.
2. **Error Hierarchy (`src/orchestrator/errors.py`):**
   - Defines `WinAIError(code, message, remediation)` and `ProviderError(WinAIError)` with `.to_dict()` serialization (verified by `tests/unit/test_phase_0073_error_hierarchy.py`).
3. **Credential Management (`src/storage/credential_vault.py`):**
   - `CredentialVault` stores and retrieves DPAPI-encrypted secrets at `~/.winai/vault.enc` (`get_credential("gnani")`) without plaintext disk persistence.
4. **Secret Redaction & Process Isolation (`src/security/audit_logger.py`, `src/windows_integration/process_runner.py`):**
   - `redact_secrets()` recursively scrubs strings, dicts, and lists using `SECRET_PATTERNS` and sensitive key-name matching.
   - `RestrictedProcessRunner` scrubs `GNANI_API_KEY`, `VACHANA_API_KEY`, and any `*_API_KEY` / `*secret*` / `*token*` / `*password*` variables from child processes.
5. **Installed Networking Stack (`pyproject.toml` / `requirements.txt`):**
   - `httpx>=0.27.0`, `websockets>=13.0`, and `pydantic>=2.10.0` are already declared and synchronized across both manifests. Zero new third-party dependencies are required for Milestone 1.

---

## 2. Proposed Files to Create or Modify

### Files to Create
1. `src/voice/__init__.py` — Public package exports for Milestone 1 voice primitives.
2. `src/voice/base.py` — Typed contracts, configuration (`GnaniConfig`), error classes (`VoiceProviderError`, `VoiceAuthError`, `VoiceRateLimitError`, `VoiceTimeoutError`, `VoiceInvalidAudioError`, `VoiceConnectionError`), normalized STT/TTS models, and RIFF/WAV container parser and sentinel normalizer (`parse_wav_container`, `normalize_wav_bytes`, `build_wav_from_pcm`).
3. `src/voice/gnani_stt.py` — `GnaniSTTProvider` implementing Prisma v2.5 REST (`POST /stt/v3` including `multi_lang_codes`) and Realtime WebSocket (`WSS /stt/v3/stream` handling `connected`, `speech_start`, `processing`, `partial_transcript`, `transcript`, `final_transcript`, `settings_updated`, and `error`).
4. `src/voice/gnani_tts.py` — `GnaniTTSProvider` implementing Timbre v2.5 & v3.0 REST (`POST /api/v1/tts/inference`) and SSE Streaming (`POST /api/v1/tts/sse`) with WAV header validation/normalization and raw PCM support.
5. `src/voice/capability_registry.py` — `CapabilityRegistry` providing independent, truthful STT and TTS readiness reporting (`UNAVAILABLE`, `UNCONFIGURED`, `CONFIGURED`, `REACHABLE`, `OPERATIONALLY_TESTED`) with dependency injection and non-destructive connectivity checks.
6. `tests/unit/test_voice_milestone1.py` — Deterministic offline unit tests covering all required STT, TTS, WAV parsing, CapabilityRegistry, and credential-redaction scenarios, plus explicit opt-in live tests (`WINAI_RUN_LIVE_GNANI_TESTS=1`).
7. `docs/voice/MILESTONE_1_IMPLEMENTATION_PLAN.md` — This implementation plan.
8. `docs/voice/MILESTONE_1_IMPLEMENTATION_REPORT.md` — Final Milestone 1 verification and completion report.

### Files to Modify
1. `src/security/audit_logger.py` — Extend `redact_secrets()` dictionary key matching to include `"authorization"`, `"proxy-authorization"`, and `"api-key"`, and extend `SECRET_PATTERNS` to scrub inline `X-API-Key-ID:` / `x-api-key:` header assignments in plain strings.
2. `tests/unit/test_audit_logger.py` — Add unit tests for case-insensitive `X-API-Key-ID`, `x-api-key`, `Authorization`, and nested credential redaction.

---

## 3. Verified Prisma v2.5 STT Protocol Details

Verified against official documentation (`https://docs.gnani.ai/api/STT/speech-to-text`, `https://docs.gnani.ai/api/STT/stt-websocket`) and live account responses:

1. **Base URLs & Authentication:**
   - REST Endpoint: `POST https://api.vachana.ai/stt/v3` (`multipart/form-data`, header `X-API-Key-ID: <api_key>`).
   - Realtime WebSocket Endpoint: `WSS wss://api.vachana.ai/stt/v3/stream` (upgrade header `x-api-key-id: <api_key>`).
2. **Supported Locales (10 BCP-47 codes):**
   - `bn-IN`, `en-IN`, `gu-IN`, `hi-IN`, `kn-IN`, `ml-IN`, `mr-IN`, `pa-IN`, `ta-IN`, `te-IN`.
3. **REST Request & Response Contract:**
   - Form fields: `language_code` (required single locale), `format` (`verbatim` | `transcribe`), `itn_native_numerals` (`true` | `false`), `enable_substitution` (`true` | `false`), `substitution_map` (JSON list up to 10 rules), `bias_list` (JSON list or CSV up to 100 single-word letter-only tokens), `bias_score` (`0.0`, `0.5`, `1.0`, `1.5`, `2.0`), and `multi_lang_codes` (optional CSV of locales for parallel transcription via `vachana-audio-intelligence-v2`).
   - File field: `audio_file` (WAV, MP3, OGG, FLAC, AAC, M4A; max 60s).
   - Response JSON (`200 OK`): `{"success": true, "request_id": "...", "transcript": "<str or dict[locale, str]>", "model": "...", "output": {"literal": ...}}`.
4. **WebSocket Framing & Event Contract:**
   - Audio input: Binary frames of signed 16-bit little-endian mono PCM (`pcm_s16le`), **1,024 bytes (512 samples) per frame** at sample rates `8000`, `16000`, `44100`, or `48000` Hz.
   - Connection headers: `x-api-key-id`, `lang_code`, `x-sample-rate`, `x-format`, `itn_native_numerals`, `x-partials` (`"true"` | `"false"`), `x-enable-substitution`, `x-substitution-map`, `x-bias-list`, `x-bias-score`, and VAD headers (`x-vad-threshold`, `x-min-silence-ms`, `x-min-speech-ms`, `x-min-segment-ms`, `x-max-speech-s`).
   - Server JSON events:
     - `connected`: handshake confirmation with `config` (`sample_rate`, `chunk_size`, `partials_enabled`, `language_code`, `format`).
     - `speech_start`: VAD detected speech start (`utterance_id`).
     - `processing`: VAD detected speech end, transcription in progress.
     - `partial_transcript`: interim segment (`utterance_id`, `seq`, `is_final: false`, `text`, `stable_text`, `volatile_text`, `audio_ms`).
     - `transcript`: final segment when `x-partials` is `false` (`text`, `segment_id`, `segment_index`, `utterance_id`, `audio_duration_ms`, `latency`, `is_final: true`).
     - `final_transcript`: final segment when `x-partials` is `true` (`text`, `segment_id`, `segment_index`, `utterance_id`, `audio_duration_ms`, `latency`, `is_final: true`).
     - `settings_updated`: confirmation of mid-session `update_settings` control frame.
     - `error`: recoverable or fatal server error (`message`, `timestamp`).
   - **Deduplication Rule:** Track `(utterance_id, segment_id, segment_index)` of emitted final transcripts so that if a server or test fixture emits both `final_transcript` and `transcript` for the same segment/utterance, only one `FINAL` event is emitted.

---

## 4. Verified Timbre v2.5 & v3.0 TTS Protocol Details

Verified against official documentation (`https://docs.gnani.ai/api/TTS/tts-inference`, `https://docs.gnani.ai/api/TTS/tts-sse`, `https://docs.gnani.ai/api/TTS/available-voices`) and live account responses:

1. **Endpoints & Authentication:**
   - REST Inference: `POST https://api.vachana.ai/api/v1/tts/inference` (Header: `X-API-Key-ID: <api_key>`, `Content-Type: application/json`).
   - SSE Streaming: `POST https://api.vachana.ai/api/v1/tts/sse` (Header: `X-API-Key-ID: <api_key>`, `Content-Type: application/json`).
2. **Models & Voices:**
   - `timbre-v2.5`: 42 locale-preferred voices (e.g., `Kaveri` for `en-IN`, `Suhana` for `te-IN`, `Poorvi` for `hi-en`, `Nalini` for `hi-IN`, `Saanvi` for `kn-IN`).
   - `timbre-v3.0`: 13 multilingual voices (e.g., `Shivani`, `Avinash`, `Lakshmi`) speaking all 10 languages and supporting inline expressive tags (`<pauses>`, `<whispers>`, `<laugh>`, etc.).
   - Voices are strictly scoped to their model (`Nalini` on `timbre-v3.0` is rejected with `400`).
3. **Request Payload:**
   - `text` (non-empty string, bounded length), `voice`, `model` (`timbre-v2.5` | `timbre-v3.0`), `language` (`auto`, `en-IN`, `hi-IN`, `hi-en`, `te-IN`, `kn-IN`, `ta-IN`, `ml-IN`, `mr-IN`, `pa-IN`, `bn-IN`, `gu-IN`), `speed` (`0.85`–`1.15`), and `audio_config` (`sample_rate`: `8000|16000|22050|24000|44100|48000`, `num_channels`: `1`, `sample_width`: `2`, `encoding`: `linear_pcm`, `container`: `wav` | `raw` | `mp3` | `ogg`).

---

## 5. Audio Format & WAV Header Normalization Decisions

1. **Empirical Discovery:**
   - Both `POST /api/v1/tts/inference` and `POST /api/v1/tts/sse` (when `container="wav"`) return valid RIFF WAVE headers where the RIFF chunk size (`bytes[4:8]`) and/or `data` subchunk size (`data_chunk_offset + 4 : data_chunk_offset + 8`) are set to the streaming sentinel `0xFFFFFFFF` (`4,294,967,295`).
2. **Chunk-Walking RIFF Parser (No Blind 44-Byte Overwrite):**
   - `parse_wav_container(data: bytes)` validates `"RIFF"` at offset 0 and `"WAVE"` at offset 8, then walks subchunks (`4-byte ID` + `4-byte little-endian uint32 size`) starting at offset 12 (`fmt `, `LIST`, `fact`, `JUNK`, `data`, etc.) rather than assuming `data` always starts at byte 36.
   - It extracts verified format metadata from the `fmt ` chunk (`audio_format`, `num_channels`, `sample_rate`, `byte_rate`, `block_align`, `bits_per_sample`) and locates the exact offset of the `data` chunk header and payload.
   - When the full audio buffer is collected and either the RIFF size or `data` chunk size contains the `0xFFFFFFFF` sentinel (or mismatches the actual buffered payload length), `normalize_wav_bytes()` patches the exact 4-byte `riff_size` (`len(buffer) - 8`) and the exact 4-byte `data_size` (`len(buffer) - data_payload_offset`) at the discovered `data` chunk offset, and computes accurate `duration_seconds = actual_data_bytes / (sample_rate * num_channels * (bits_per_sample // 8))`.
3. **Raw PCM Mode (`container="raw"`):**
   - When `container="raw"` is requested, `GnaniTTSProvider` returns the raw PCM payload explicitly labeled as `container="raw"`, `mime_type="application/octet-stream"`, `encoding="linear_pcm"` (never mislabeled as WAV), and provides `build_wav_from_pcm()` only when an explicit WAV conversion is requested.

---

## 6. Security Controls

1. **Zero Secret Leakage:**
   - All exceptions raised by `GnaniSTTProvider`, `GnaniTTSProvider`, and `CapabilityRegistry` pass error text through `redact_secrets()` before constructing the exception message.
   - Request headers (`X-API-Key-ID`, `x-api-key-id`) are constructed inside request scope and never stored on exception objects, result models, or `CapabilityRegistry` snapshots.
2. **Bounded Resource Consumption:**
   - `GnaniSTTProvider` enforces `max_audio_bytes` (default 15 MB) and `max_ws_messages` / `timeout_seconds` to prevent unbounded memory growth.
   - `GnaniTTSProvider` enforces `max_text_chars` (default 5,000 chars) and `max_response_bytes` (default 25 MB) across both REST and SSE streams.
3. **Non-Destructive Health Check:**
   - `CapabilityRegistry` verifies Gnani reachability via `GET https://api.vachana.ai/stt/v3/batch/jobs?limit=1` (0 inference credits, no audio upload, no speech synthesis) and only runs when `verify_connectivity()` is explicitly invoked.

---

## 7. Test Plan & Acceptance Criteria

1. **Offline Deterministic Unit Tests (`tests/unit/test_voice_milestone1.py`):**
   - **STT (16+ tests):** Missing API key, valid partial transcript, final event `transcript`, final event `final_transcript`, partial followed by final, duplicate final deduplication, empty transcript, empty audio rejection, oversized audio rejection, malformed JSON handling, unexpected event type handling, `401` auth failure, `429` rate limit, connection failure, timeout, WebSocket resource cleanup on error, and credential redaction in exceptions.
   - **TTS (14+ tests):** Missing API key, valid complete WAV response, raw PCM response, SSE streaming response (`start` → `chunk` → `complete`), WAV header with `0xFFFFFFFF` sentinel sizes (including non-44-byte header with extra `LIST`/`JUNK` chunk), accurate final WAV size & duration after normalization, empty text / empty audio response, invalid content-type / corrupt audio bytes, model-voice mismatch validation, `401` auth failure, `429` rate limit, timeout, interrupted SSE stream (missing `complete`), oversized response bound, and credential redaction.
   - **CapabilityRegistry (8+ tests):** Unconfigured state, configured-unverified state, reachable state after non-destructive health check, failed health check reason, STT available while TTS unavailable, TTS available while STT unavailable, zero secrets in serialized output, and zero network calls during initialization.
2. **Opt-In Live API Tests:**
   - Guarded by `@pytest.mark.skipif(os.environ.get("WINAI_RUN_LIVE_GNANI_TESTS") != "1", ...)` so normal test runs are 100% offline.
3. **Regression Suite:**
   - Full Milestone 0 hermetic suite (`426+` tests), `scripts/check_dependency_drift.py`, and `scripts/diagnostics.py` must pass with exit code `0`.
