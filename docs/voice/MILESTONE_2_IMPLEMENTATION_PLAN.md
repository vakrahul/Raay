# Milestone 2 Implementation Plan — WinAI-OE Voice I/O Foundation

**Status:** Active Implementation Plan (Milestone 2 Only)  
**Scope:** `SpeechInputManager`, `SpeechOutputManager`, `VoiceIOSession` lifecycle coordinator, audio device enumeration/selection (`WinMMAudioBackend` + injectable backend protocol), deterministic offline fake-device tests, and opt-in physical hardware tests (`WINAI_RUN_AUDIO_DEVICE_TESTS=1`).

---

## 1. Existing Architecture Discovered

1. **Milestone 1 Voice Modules (`src/voice/`):**
   - `src/voice/base.py`: Provides `GnaniConfig`, `BaseSTTProvider`, `BaseTTSProvider`, `STTTranscriptEvent`, `STTResult`, `TTSRequest`, `TTSAudioConfig`, `TTSAudioResult`, `TTSStreamChunk`, `WavHeaderMetadata`, `parse_wav_container()`, `normalize_wav_bytes()`, `build_wav_from_pcm()`, and the sanitized `VoiceProviderError` hierarchy.
   - `src/voice/gnani_stt.py`: `GnaniSTTProvider` accepts an `AsyncIterator[bytes]` in `stream_pcm()` (streaming 1,024-byte 16-bit mono PCM frames) and `bytes` in `transcribe_bytes()`.
   - `src/voice/gnani_tts.py`: `GnaniTTSProvider` returns validated `TTSAudioResult` (`container="wav"` with repaired RIFF/data sizes or `container="raw"` with explicit PCM parameters).
   - `src/voice/capability_registry.py`: `CapabilityRegistry` tracks STT and TTS readiness independently.
2. **Packaging & Runtime Constraints (`.github/workflows/build-exe.yml`, `pyproject.toml`, `requirements.txt`):**
   - Target platform is Windows 10/11 (64-bit) running Python 3.11 / 3.12 as a standard (non-elevated) desktop user.
   - Standalone builds use PyInstaller (`build-exe.yml`) installing from `requirements.txt`.
   - `scripts/check_dependency_drift.py` and `scripts/check_packaging.py` enforce strict manifest parity.

---

## 2. Audio Backend Selection & Rationale

### Selected Backend: Native Windows Multimedia API (`winmm.dll` via `ctypes`) + Injectable `AudioBackend` Protocol

1. **Why `winmm.dll` (`waveIn*` / `waveOut*`) via `ctypes`:**
   - **Zero Third-Party Dependency Drift:** `winmm.dll` is a core Windows OS system library present on every Windows 10/11 machine and inside PyInstaller executables without bundling PortAudio C-extensions.
   - **Deterministic Hardware Cancellation (`waveInReset` / `waveOutReset`):** Unlike `winsound.PlaySound`, `winmm.dll` `waveOutWrite` + `waveOutReset` supports both WAV payloads and raw PCM buffers, tracks actual hardware completion via `WHDR_DONE`, and aborts active speaker playback in `<10ms` when `stop()` or `cancel()` is invoked.
   - **GIL-Safe Multi-Buffer Capture (`waveInOpen`):** Using a pre-allocated ring of 8 `WAVEHDR` buffers polled on a dedicated background worker thread (signaled via a Win32 event handle `CreateEventW` passed with `CALLBACK_EVENT`) avoids Python C-callback GIL deadlocks while delivering exact 1,024-byte frames to the asyncio consumer queue.
2. **Injectable Backend Abstraction (`BaseAudioInputBackend`, `BaseAudioOutputBackend`):**
   - `SpeechInputManager` and `SpeechOutputManager` depend on a small, explicit backend interface (`list_devices`, `open_stream`, `read_frame`, `write_pcm`, `stop_stream`, `close_stream`), defaulting to `WinMMAudioInputBackend` and `WinMMAudioOutputBackend` on Windows while allowing `FakeAudioInputBackend` and `FakeAudioOutputBackend` in unit tests.

---

## 3. Proposed Files to Create or Modify

### Files to Create
1. `src/voice/audio_types.py` — Typed contracts for audio I/O:
   - `CaptureState` (`IDLE`, `STARTING`, `RECORDING`, `STOPPING`, `STOPPED`, `ERROR`)
   - `PlaybackState` (`IDLE`, `STARTING`, `PLAYING`, `STOPPING`, `STOPPED`, `ERROR`)
   - `VoiceSessionState` (`IDLE`, `ACTIVE`, `CAPTURING`, `TRANSCRIBING`, `SPEAKING`, `STOPPING`, `CANCELLED`, `CLOSED`, `ERROR`)
   - `QueueOverflowPolicy` (`DROP_OLDEST`, `DROP_NEWEST`, `RAISE_ERROR`)
   - `AudioFormatConfig` (sample rate, channels, sample width, frame bytes, bounded queue capacity)
   - `AudioDeviceInfo` (`device_id`, `name`, `kind`, `max_channels`, `is_default`)
   - `AudioFrame` (`pcm_bytes`, `sequence`, `sample_rate`, `channels`, `sample_width`, `rms`, `duration_ms`, `timestamp`)
   - `PlaybackOutcome` (`completed`, `cancelled`, `duration_played_ms`, `bytes_played`, `device_id`)
   - Sanitized audio errors (`AudioDeviceError`, `AudioDeviceUnavailableError`, `AudioPermissionError`, `AudioStateError`, `AudioFormatError`, `AudioQueueOverflowError`).
2. `src/voice/audio_devices.py` — `BaseAudioInputBackend`, `BaseAudioOutputBackend`, and native Windows `WinMMAudioInputBackend` / `WinMMAudioOutputBackend` (`waveInGetNumDevs`, `waveInGetDevCapsW`, `waveInOpen` with `CALLBACK_EVENT`, `waveOutGetNumDevs`, `waveOutGetDevCapsW`, `waveOutOpen` with `CALLBACK_EVENT`, `waveOutReset`).
3. `src/voice/speech_input.py` — `SpeechInputManager` implementing device enumeration, device selection, explicit `start()`, bounded thread-safe queue (`collections.deque` + `asyncio.Event` / `threading.Lock`), `read_frame()`, `frames()` async generator, `stop()`, `cancel()`, `close()`, and state/metrics reporting.
4. `src/voice/speech_output.py` — `SpeechOutputManager` implementing device enumeration, device selection, `play(audio_result)` validating `TTSAudioResult` (`container="wav"` via `parse_wav_container` or `container="raw"` with explicit PCM parameters), non-overlapping playback guard, `stop()`, `cancel()`, `close()`, and accurate completion verification.
5. `src/voice/session.py` — `VoiceIOSession` coordinating `SpeechInputManager`, `SpeechOutputManager`, and optional caller-triggered `BaseSTTProvider` / `BaseTTSProvider` operations with hard cancellation guarantees (stops capture, aborts playback, cancels session-owned async tasks, closes session-owned streams, never mutates or cancels shared providers or other sessions).
6. `tests/unit/test_voice_milestone2.py` — Deterministic offline unit tests for `SpeechInputManager` (15+ scenarios), `SpeechOutputManager` (15+ scenarios), and `VoiceIOSession` (10+ scenarios) using fake backends, plus opt-in physical hardware tests guarded by `WINAI_RUN_AUDIO_DEVICE_TESTS=1`.
7. `docs/voice/MILESTONE_2_IMPLEMENTATION_PLAN.md` — This implementation plan.
8. `docs/voice/MILESTONE_2_IMPLEMENTATION_REPORT.md` — Final Milestone 2 implementation report.

### Files to Modify
1. `src/voice/__init__.py` — Export Milestone 2 classes, states, and error types alongside Milestone 1 exports.

---

## 4. Resource-Lifecycle, Threading & Cancellation Design

1. **Callback/Worker-to-Asyncio Bridge (`SpeechInputManager`):**
   - Audio capture runs on a dedicated daemon worker thread only while `state == CaptureState.RECORDING`.
   - Captured frames are placed into a bounded buffer (`max_queue_frames`, default `256` frames = ~8.1 seconds at 32ms/frame).
   - When the consumer is slower than the producer, `QueueOverflowPolicy.DROP_OLDEST` (default) evicts the oldest unread frame and increments `dropped_frames_count` so memory never grows unboundedly and the producer thread never blocks on the consumer. (`DROP_NEWEST` and `RAISE_ERROR` are also supported via configuration).
   - An `asyncio.Event` notified via `loop.call_soon_threadsafe()` wakes any awaiting `read_frame()` coroutine without polling delays.
2. **Idempotent `stop()` and `cancel()` (`SpeechInputManager` & `SpeechOutputManager`):**
   - Protected by a state lock so concurrent `start()`, `stop()`, and `cancel()` calls serialize safely.
   - Calling `stop()` or `cancel()` when already `STOPPED` or `IDLE` is a safe no-op.
   - Calling `cancel()` during `STARTING` or `RECORDING` immediately signals the worker stop event, invokes `backend.stop_stream()` / `backend.close_stream()`, drains/clears any unread frames so **zero frames are delivered after cancellation**, and wakes all waiting consumers with `None` (EOF) or `asyncio.CancelledError`.
3. **Zero Disk Persistence & Explicit Transmission Only:**
   - All captured and synthesized audio stays exclusively in bounded in-memory buffers (`bytes` / `bytearray`). No temporary `.wav` files are written to disk.
   - `SpeechInputManager` never contacts `GnaniSTTProvider`; audio is only transmitted to Prisma when the caller explicitly invokes `session.transcribe_captured_audio()` or `session.stream_capture_to_stt()`.

---

## 5. Test Plan & Acceptance Criteria

1. **Offline Unit Tests (`tests/unit/test_voice_milestone2.py`):**
   - All 40+ required test cases across `SpeechInputManager`, `SpeechOutputManager`, and `VoiceIOSession` run 100% offline using `FakeAudioInputBackend` and `FakeAudioOutputBackend`.
2. **Opt-In Hardware Device Tests (`WINAI_RUN_AUDIO_DEVICE_TESTS=1`):**
   - Skipped by default when `WINAI_RUN_AUDIO_DEVICE_TESTS != "1"`.
   - When enabled, enumerates WinMM devices, performs a bounded 150ms capture if a single input device or `WINAI_TEST_INPUT_DEVICE_ID` is specified, and verifies device release.
3. **Regression & Hygiene:**
   - Full Milestone 0 + Milestone 1 hermetic suite (`443+` tests), `scripts/check_dependency_drift.py`, `scripts/diagnostics.py`, and `ruff check` must pass with exit code `0`.
