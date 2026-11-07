"""Gnani Prisma v2.5 Speech-to-Text (STT) Provider Adapter (Milestone 1 & Phase 2).

Supports:
- Realtime WebSocket streaming (`WSS /stt/v3/stream`) with exact 1,024-byte PCM framing,
  monotonic clock drift-corrected real-time pacing (32 ms @ 16 kHz, 64 ms @ 8 kHz),
  VAD tuning headers (`x-vad-threshold`, `x-min-silence-ms`, `x-min-speech-ms`,
  `x-min-segment-ms`, `x-max-speech-s`), mid-session `update_settings` (`substitution`,
  `bias`), and strict handling of the 5 documented server message types
  (`connected`, `processing`, `transcript`, `error`, `settings_updated`).
- Single-connection segment stitcher (`SegmentStitcher`) with configurable
  `vad_min_silence_ms` (default 350 ms) and `segment_stitch_window_ms` (default 650 ms),
  plus an optional reconnect-based `VADProfileController` behind `reconnect_on_profile_switch`.
- Unicode-aware `bias_list` sanitizer (`sanitize_bias_list`) accepting Unicode letters (`L*`)
  and Indic combining vowel/virama marks (`Mn`, `Mc`), rejecting spaces/digits/hyphens/punctuation,
  and logging every dropped entry.
- Strict ITN rule: `format=transcribe` (`x-format: transcribe`) is enabled only when
  `language_code` is `hi-IN` or `en-IN` and `bias_list` is empty; otherwise `format=verbatim`.
- Synchronous REST transcription (`POST /stt/v3`) and non-destructive reachability check
  (`GET /stt/v3/batch/jobs?limit=1`).
"""

import asyncio
import contextlib
import json
import logging
import time
import unicodedata
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Optional, Set, Tuple

import httpx
import websockets

from src.providers.resilience import CircuitBreaker
from src.voice.base import (
    ITN_SUPPORTED_LANGUAGES,
    SUPPORTED_BIAS_SCORES,
    SUPPORTED_STT_LANGUAGES,
    SUPPORTED_STT_SAMPLE_RATES,
    BaseSTTProvider,
    GnaniConfig,
    STTResult,
    STTTranscriptEvent,
    TranscriptEventKind,
    VoiceAuthError,
    VoiceConfigError,
    VoiceConnectionError,
    VoiceInvalidAudioError,
    VoiceProviderError,
    VoiceRateLimitError,
    VoiceTimeoutError,
    sanitize_error_message,
)

logger = logging.getLogger(__name__)

PCM_FRAME_BYTES = 1024
PCM_SAMPLES_PER_FRAME = 512  # 1,024 bytes / 2 bytes per 16-bit mono sample

DOCUMENTED_WS_MESSAGE_TYPES: Set[str] = {
    "connected",
    "processing",
    "transcript",
    "error",
    "settings_updated",
}

DEFAULT_VOICE_BIAS_WORDS: List[str] = [
    # Hindi (Devanagari + Romanized)
    "रुको",
    "रोको",
    "वापस",
    "नोटपैड",
    "लिखो",
    "साफ़",
    "ruko",
    "rukko",
    "roko",
    "wapas",
    "likho",
    # Tamil (Tamil script + Romanized)
    "நில்லு",
    "நிறுத்து",
    "நோட்பேட்",
    "எழுது",
    "nillu",
    "aapo",
    "ezhudhu",
    # Telugu (Telugu script + Romanized)
    "ఆగు",
    "చెయ్యకు",
    "రాయి",
    "నోట్",
    "ప్యాడ్",
    "aagu",
    "cheyyaku",
    "rayi",
    # English commands, apps & editor terms
    "stop",
    "wait",
    "hold",
    "undo",
    "cancel",
    "clear",
    "clearer",
    "notepad",
    "calculator",
    "function",
    "return",
    "python",
]


def is_valid_bias_word(word: str) -> Tuple[bool, str]:
    """Validate a single word against Gnani bias_list rules (Unicode L* + Mn/Mc only).

    Accepts Unicode letters (categories starting with 'L') plus Indic non-spacing and
    spacing combining marks ('Mn', 'Mc') required for Hindi, Tamil, and Telugu vowel signs.
    Rejects empty strings, whitespace, digits, hyphens, punctuation, symbols, and format chars.
    """
    if not isinstance(word, str):
        return False, "not a string"
    normalized = unicodedata.normalize("NFC", word.strip())
    if not normalized:
        return False, "empty word"

    has_letter = False
    for ch in normalized:
        cat = unicodedata.category(ch)
        if cat.startswith("L"):
            has_letter = True
        elif cat in ("Mn", "Mc"):
            continue
        else:
            return False, f"invalid character {ch!r} (Unicode category {cat})"

    if not has_letter:
        return False, "contains no Unicode letter character"
    return True, ""


def sanitize_bias_list(
    raw_words: Optional[Iterable[str]],
    max_words: int = 100,
) -> Tuple[List[str], List[Dict[str, str]]]:
    """Sanitize a candidate bias_list and log every dropped entry.

    Returns (accepted_words, dropped_entries).
    """
    if not raw_words:
        return [], []

    accepted: List[str] = []
    dropped: List[Dict[str, str]] = []
    seen_lower: Set[str] = set()

    for raw in raw_words:
        raw_str = str(raw) if raw is not None else ""
        cleaned = unicodedata.normalize("NFC", raw_str.strip())
        valid, reason = is_valid_bias_word(cleaned)
        if not valid:
            logger.warning("Dropped invalid bias_list entry %r: %s", raw_str, reason)
            dropped.append({"entry": raw_str, "reason": reason})
            continue

        dedup_key = cleaned.lower()
        if dedup_key in seen_lower:
            continue

        if len(accepted) >= max_words:
            cap_reason = f"exceeds max_words ({max_words})"
            logger.warning("Dropped bias_list entry %r: %s", cleaned, cap_reason)
            dropped.append({"entry": cleaned, "reason": cap_reason})
            continue

        seen_lower.add(dedup_key)
        accepted.append(cleaned)

    return accepted, dropped


def resolve_stt_output_format(
    language_code: str,
    requested_format: Optional[str] = None,
    has_bias_list: bool = False,
) -> str:
    """Resolve STT format per official Gnani docs:
    - If bias_list is non-empty -> MUST be 'verbatim'.
    - If language_code is 'hi-IN' or 'en-IN' and bias_list is empty -> 'transcribe' (ITN)
      unless caller explicitly requested 'verbatim'.
    - For all other locales (e.g. 'ta-IN', 'te-IN') -> always 'verbatim' (ITN never claimed).
    """
    if requested_format is not None and requested_format not in ("verbatim", "transcribe"):
        raise VoiceConfigError(
            code="STT_INVALID_FORMAT",
            message=(
                f"Unsupported STT format '{requested_format}'. Must be 'verbatim' or 'transcribe'."
            ),
        )
    if has_bias_list:
        return "verbatim"
    if language_code in ITN_SUPPORTED_LANGUAGES:
        return requested_format if requested_format is not None else "transcribe"
    return "verbatim"


class VADProfile(str, Enum):
    """VAD profiles for streaming STT."""

    UNIFIED = "UNIFIED"
    COMMAND = "COMMAND"
    STOP = "STOP"


class SegmentStitcher:
    """Stitches consecutive WebSocket transcript segments on a single connection.

    Because VAD headers (`x-min-silence-ms`, `x-vad-threshold`) are fixed per WebSocket
    connection in `docs.gnani.ai`, keeping `x-min-silence-ms` at 300–400 ms delivers fast
    interrupt/stop segments while `SegmentStitcher` joins consecutive segments whose gap
    is within `stitch_window_ms` so full multi-word commands still assemble cleanly.
    """

    def __init__(
        self,
        min_silence_ms: int = 350,
        stitch_window_ms: int = 650,
    ) -> None:
        self.min_silence_ms = max(100, min(2000, int(min_silence_ms)))
        self.stitch_window_ms = max(50, min(5000, int(stitch_window_ms)))
        self._parts: List[str] = []
        self._segment_ids: List[str] = []
        self._latencies_ms: List[float] = []
        self._last_segment_monotonic: Optional[float] = None
        logger.info(
            "Initialized SegmentStitcher(min_silence_ms=%d, stitch_window_ms=%d)",
            self.min_silence_ms,
            self.stitch_window_ms,
        )

    @property
    def has_pending(self) -> bool:
        return bool(self._parts)

    def add_segment(
        self,
        text: str,
        segment_id: Optional[str] = None,
        latency_ms: Optional[float] = None,
        arrival_monotonic: Optional[float] = None,
    ) -> Tuple[str, bool]:
        """Add a transcript segment; returns (current_stitched_text, stitched_with_previous)."""
        now = arrival_monotonic if arrival_monotonic is not None else time.monotonic()
        clean = (text or "").strip()
        if not clean:
            return self.current_text(), False

        stitched_prev = False
        if self._parts and self._last_segment_monotonic is not None:
            gap_ms = (now - self._last_segment_monotonic) * 1000.0
            if gap_ms <= self.stitch_window_ms:
                self._parts.append(clean)
                stitched_prev = True
            else:
                self._parts = [clean]
                self._segment_ids.clear()
                self._latencies_ms.clear()
        else:
            self._parts = [clean]

        if segment_id:
            self._segment_ids.append(segment_id)
        if latency_ms is not None:
            self._latencies_ms.append(float(latency_ms))
        self._last_segment_monotonic = now
        return self.current_text(), stitched_prev

    def should_flush(self, now_monotonic: Optional[float] = None) -> bool:
        if not self._parts or self._last_segment_monotonic is None:
            return False
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        return ((now - self._last_segment_monotonic) * 1000.0) >= self.stitch_window_ms

    def current_text(self) -> str:
        return " ".join(self._parts).strip()

    def flush(self) -> Dict[str, Any]:
        text = self.current_text()
        seg_ids = list(self._segment_ids)
        lats = list(self._latencies_ms)
        self._parts.clear()
        self._segment_ids.clear()
        self._latencies_ms.clear()
        self._last_segment_monotonic = None
        return {
            "text": text,
            "segment_ids": seg_ids,
            "primary_segment_id": seg_ids[-1] if seg_ids else None,
            "latency_ms": lats[-1] if lats else None,
            "min_silence_ms": self.min_silence_ms,
            "stitch_window_ms": self.stitch_window_ms,
        }


class VADProfileController:
    """Manages VAD profile parameters (`COMMAND` vs `STOP`) and optional reconnect flag."""

    def __init__(self, config: GnaniConfig) -> None:
        self.config = config
        self.active_profile: VADProfile = VADProfile.UNIFIED
        self.reconnect_count: int = 0

    def headers_for_profile(self, profile: Optional[VADProfile] = None) -> Dict[str, str]:
        prof = profile or self.active_profile
        if not self.config.reconnect_on_profile_switch or prof == VADProfile.UNIFIED:
            silence_ms = self.config.vad_min_silence_ms
            threshold = self.config.vad_threshold
        elif prof == VADProfile.COMMAND:
            silence_ms = max(600, self.config.vad_min_silence_ms)
            threshold = self.config.vad_threshold
        else:  # VADProfile.STOP
            silence_ms = 300
            threshold = self.config.vad_threshold

        hdrs = {
            "x-vad-threshold": str(max(0.0, min(1.0, float(threshold)))),
            "x-min-silence-ms": str(max(100, min(2000, int(silence_ms)))),
            "x-min-speech-ms": str(max(50, min(2000, int(self.config.vad_min_speech_ms)))),
            "x-min-segment-ms": str(max(100, min(1000, int(self.config.vad_min_segment_ms)))),
        }
        if self.config.vad_max_speech_s is not None:
            hdrs["x-max-speech-s"] = str(max(5.0, min(120.0, float(self.config.vad_max_speech_s))))
        return hdrs

    def switch_profile(self, target_profile: VADProfile) -> bool:
        """Switch profile; returns True ONLY if `reconnect_on_profile_switch` is enabled."""
        if target_profile == self.active_profile:
            return False
        self.active_profile = target_profile
        if self.config.reconnect_on_profile_switch:
            self.reconnect_count += 1
            logger.info(
                "Switching VAD profile to %s via WebSocket reconnect (#%d)",
                target_profile.value,
                self.reconnect_count,
            )
            return True
        logger.info(
            "VAD profile %s requested on single-connection mode (using SegmentStitcher)",
            target_profile.value,
        )
        return False


class GnaniSTTProvider(BaseSTTProvider):
    """Provider adapter for Gnani Prisma v2.5 Speech-to-Text."""

    def __init__(
        self,
        config: Optional[GnaniConfig] = None,
        http_transport: Optional[httpx.AsyncBaseTransport] = None,
        ws_connector: Optional[Callable[..., Any]] = None,
    ):
        self.config = config or GnaniConfig.from_env_or_vault()
        self._http_transport = http_transport
        self._ws_connector = ws_connector or websockets.connect
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        self._operationally_tested: bool = False
        self.last_dropped_bias_entries: List[Dict[str, str]] = []
        self.vad_controller = VADProfileController(self.config)

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    @property
    def is_operationally_tested(self) -> bool:
        return self._operationally_tested

    # ------------------------------------------------------------------
    # Validation Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_language(language_code: str) -> str:
        code = (language_code or "").strip()
        if code not in SUPPORTED_STT_LANGUAGES:
            allowed = sorted(SUPPORTED_STT_LANGUAGES)
            raise VoiceConfigError(
                code="STT_INVALID_LANGUAGE",
                message=f"Unsupported STT language_code '{code}'. Must be one of {allowed}.",
            )
        return code

    @staticmethod
    def _validate_sample_rate(sample_rate: int) -> int:
        if sample_rate not in SUPPORTED_STT_SAMPLE_RATES:
            allowed = sorted(SUPPORTED_STT_SAMPLE_RATES)
            raise VoiceConfigError(
                code="STT_INVALID_SAMPLE_RATE",
                message=f"Unsupported STT sample_rate {sample_rate}. Must be one of {allowed}.",
            )
        return sample_rate

    def _validate_audio_bytes(self, audio_bytes: bytes) -> None:
        if not audio_bytes or len(audio_bytes) == 0:
            raise VoiceInvalidAudioError(
                code="STT_EMPTY_AUDIO",
                message="Audio payload is empty (0 bytes).",
            )
        if len(audio_bytes) > self.config.max_audio_bytes:
            max_b = self.config.max_audio_bytes
            raise VoiceInvalidAudioError(
                code="STT_AUDIO_TOO_LARGE",
                message=(
                    f"Audio payload ({len(audio_bytes)} bytes) exceeds max_audio_bytes ({max_b})."
                ),
            )
        # If WAV header is present, ensure data payload isn't empty
        if len(audio_bytes) >= 44 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
            if len(audio_bytes) <= 44 or all(
                b == 0 for b in audio_bytes[44 : min(len(audio_bytes), 2048)]
            ):
                # Check if entire body after 44-byte header is empty
                if len(audio_bytes) <= 44:
                    raise VoiceInvalidAudioError(
                        code="STT_EMPTY_WAV_DATA",
                        message="WAV file contains a header with 0 audio frames.",
                    )

    def _map_http_error(self, status_code: int, body_text: str, secret: str) -> VoiceProviderError:
        safe_body = sanitize_error_message(body_text[:400], explicit_secret=secret)
        if status_code == 401:
            return VoiceAuthError(
                code="STT_AUTH_FAILED",
                message=f"Gnani STT authentication failed (401): {safe_body}",
                remediation="Verify that GNANI_API_KEY is a valid Vachana API key.",
                status_code=401,
                explicit_secret=secret,
            )
        if status_code == 403:
            return VoiceAuthError(
                code="STT_FORBIDDEN",
                message=f"Gnani STT access forbidden or quota exhausted (403): {safe_body}",
                remediation="Check account permissions and credit balance on app.gnani.ai/voice.",
                status_code=403,
                explicit_secret=secret,
            )
        if status_code == 429:
            return VoiceRateLimitError(
                code="STT_RATE_LIMITED",
                message=f"Gnani STT rate limit exceeded (429): {safe_body}",
                remediation="Reduce request frequency or wait before retrying.",
                status_code=429,
                explicit_secret=secret,
            )
        if status_code in (400, 422):
            return VoiceInvalidAudioError(
                code="STT_BAD_REQUEST",
                message=(
                    f"Gnani STT rejected request parameters or audio ({status_code}): {safe_body}"
                ),
                status_code=status_code,
                explicit_secret=secret,
            )
        return VoiceProviderError(
            code=f"STT_HTTP_{status_code}",
            message=f"Gnani STT server error ({status_code}): {safe_body}",
            status_code=status_code,
            explicit_secret=secret,
        )

    # ------------------------------------------------------------------
    # Non-Destructive Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> Tuple[bool, str]:
        """Verify Gnani API key validity via zero-cost GET /stt/v3/batch/jobs?limit=1."""
        if not self.config.is_configured:
            return False, "Gnani API key is not configured."
        secret = self.config.get_secret_key()
        url = f"{self.config.rest_base_url}/stt/v3/batch/jobs?limit=1"
        try:
            async with httpx.AsyncClient(
                transport=self._http_transport,
                timeout=self.config.connect_timeout_seconds,
            ) as client:
                res = await client.get(url, headers={"X-API-Key-ID": secret})
            if res.status_code == 200:
                return True, "Authenticated and reachable via GET /stt/v3/batch/jobs."
            err = self._map_http_error(res.status_code, res.text, secret)
            return False, err.message
        except httpx.TimeoutException:
            return False, "Timed out connecting to Gnani STT endpoint."
        except Exception as e:
            clean_err = sanitize_error_message(str(e), explicit_secret=secret)
            return (False, f"Network error probing Gnani STT: {clean_err}")

    # ------------------------------------------------------------------
    # Synchronous REST Transcription (`POST /stt/v3`)
    # ------------------------------------------------------------------

    async def transcribe_bytes(
        self,
        audio_bytes: bytes,
        language_code: str = "en-IN",
        filename: str = "audio.wav",
        output_format: str = "transcribe",
        itn_native_numerals: bool = False,
        enable_substitution: bool = False,
        substitution_map: Optional[List[Dict[str, Any]]] = None,
        bias_list: Optional[List[str]] = None,
        bias_score: float = 0.0,
    ) -> STTResult:
        """Transcribe audio clip (<=60s) via POST /stt/v3."""
        if not self.circuit_breaker.allow_request():
            raise VoiceProviderError(
                code="STT_CIRCUIT_OPEN",
                message="Gnani STT circuit breaker is OPEN due to consecutive upstream failures.",
            )

        secret = self.config.get_secret_key()
        lang = self._validate_language(language_code)
        self._validate_audio_bytes(audio_bytes)

        clean_words: List[str] = []
        if bias_list and bias_score in SUPPORTED_BIAS_SCORES and bias_score > 0.0:
            clean_words, dropped = sanitize_bias_list(bias_list)
            self.last_dropped_bias_entries = dropped

        effective_format = resolve_stt_output_format(
            language_code=lang,
            requested_format=output_format,
            has_bias_list=bool(clean_words),
        )

        form_data: Dict[str, str] = {
            "language_code": lang,
            "format": effective_format,
            "itn_native_numerals": (
                "true" if (itn_native_numerals and effective_format == "transcribe") else "false"
            ),
        }

        if enable_substitution and substitution_map:
            form_data["enable_substitution"] = "true"
            form_data["substitution_map"] = json.dumps(substitution_map[:10])

        if clean_words:
            form_data["bias_list"] = json.dumps(clean_words)
            form_data["bias_score"] = str(bias_score)

        url = f"{self.config.rest_base_url}/stt/v3"
        t0 = time.perf_counter()

        try:
            async with httpx.AsyncClient(
                transport=self._http_transport,
                timeout=self.config.request_timeout_seconds,
            ) as client:
                res = await client.post(
                    url,
                    headers={"X-API-Key-ID": secret},
                    data=form_data,
                    files={"audio_file": (filename, audio_bytes, "audio/wav")},
                )
        except httpx.TimeoutException as e:
            self.circuit_breaker.record_failure()
            req_to = self.config.request_timeout_seconds
            raise VoiceTimeoutError(
                code="STT_TIMEOUT",
                message=f"Gnani STT REST request timed out after {req_to}s.",
                explicit_secret=secret,
            ) from e
        except httpx.HTTPError as e:
            self.circuit_breaker.record_failure()
            raise VoiceConnectionError(
                code="STT_CONNECTION_ERROR",
                message=f"Gnani STT network error: {e}",
                explicit_secret=secret,
            ) from e

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        if res.status_code != 200:
            if res.status_code >= 500:
                self.circuit_breaker.record_failure()
            raise self._map_http_error(res.status_code, res.text, secret)

        try:
            payload = res.json()
        except Exception as e:
            self.circuit_breaker.record_failure()
            raise VoiceProviderError(
                code="STT_MALFORMED_JSON",
                message="Gnani STT returned invalid JSON payload.",
                explicit_secret=secret,
            ) from e

        if not isinstance(payload, dict) or payload.get("success") is False:
            err_detail = payload.get("error", {}) if isinstance(payload, dict) else {}
            err_msg = err_detail.get("message") if isinstance(err_detail, dict) else str(payload)
            raise VoiceProviderError(
                code="STT_API_ERROR",
                message=f"Gnani STT returned failure payload: {err_msg}",
                explicit_secret=secret,
            )

        raw_transcript = payload.get("transcript", "")
        primary_text = str(raw_transcript or "").strip()

        literal_raw = (
            (payload.get("output") or {}).get("literal")
            if isinstance(payload.get("output"), dict)
            else None
        )
        if isinstance(literal_raw, str):
            literal_text = literal_raw.strip() or None
        else:
            literal_text = None

        proc_sec = payload.get("processing_time")
        e2e_sec = payload.get("end_to_end_latency")

        self.circuit_breaker.record_success()
        self._operationally_tested = True

        segment = STTTranscriptEvent(
            kind=TranscriptEventKind.FINAL,
            text=primary_text,
            stable_text=primary_text,
            literal_text=literal_text,
            is_final=True,
            language_code=lang,
            latency_ms=round(float(e2e_sec) * 1000.0, 2)
            if isinstance(e2e_sec, (int, float))
            else elapsed_ms,
        )

        return STTResult(
            success=True,
            transcript=primary_text,
            literal_transcript=literal_text,
            language_code=lang,
            model=payload.get("model"),
            request_id=payload.get("request_id"),
            processing_time_ms=round(float(proc_sec) * 1000.0, 2)
            if isinstance(proc_sec, (int, float))
            else None,
            end_to_end_latency_ms=round(float(e2e_sec) * 1000.0, 2)
            if isinstance(e2e_sec, (int, float))
            else elapsed_ms,
            segments=[segment],
        )

    # ------------------------------------------------------------------
    # WebSocket Event Normalization & Deduplication (Exact 5 Message Types)
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_ws_message(
        raw_message: str,
        seen_final_keys: Set[str],
        language_code: str = "en-IN",
        secret: Optional[str] = None,
    ) -> Optional[STTTranscriptEvent]:
        """Parse and normalize a single WebSocket JSON text frame from Prisma STT.

        Strictly handles only the 5 documented server message types in `stt-websocket.md`:
          `connected`, `processing`, `transcript`, `error`, `settings_updated`.
        Any other message type is logged and ignored (returns `None`).
        """
        try:
            msg = json.loads(raw_message)
        except Exception:
            return STTTranscriptEvent(
                kind=TranscriptEventKind.ERROR,
                error_message="Malformed JSON frame received from STT WebSocket.",
            )

        if not isinstance(msg, dict):
            return STTTranscriptEvent(
                kind=TranscriptEventKind.ERROR,
                error_message="Unexpected non-object JSON frame received from STT WebSocket.",
            )

        msg_type = str(msg.get("type") or "").strip()
        timestamp = msg.get("timestamp")

        if msg_type not in DOCUMENTED_WS_MESSAGE_TYPES:
            logger.warning("Ignoring undocumented Gnani STT WebSocket message type: %r", msg_type)
            return None

        if msg_type == "connected":
            return STTTranscriptEvent(
                kind=TranscriptEventKind.CONNECTED,
                language_code=language_code,
                timestamp=timestamp,
                metadata=msg.get("config") if isinstance(msg.get("config"), dict) else {},
            )

        if msg_type == "processing":
            return STTTranscriptEvent(
                kind=TranscriptEventKind.PROCESSING,
                language_code=language_code,
                timestamp=timestamp,
            )

        if msg_type == "settings_updated":
            return STTTranscriptEvent(
                kind=TranscriptEventKind.SETTINGS_UPDATED,
                language_code=language_code,
                timestamp=timestamp,
                metadata=msg.get("config") if isinstance(msg.get("config"), dict) else {},
            )

        if msg_type == "error":
            raw_err = str(msg.get("message") or "Unknown STT stream error")
            return STTTranscriptEvent(
                kind=TranscriptEventKind.ERROR,
                error_message=sanitize_error_message(raw_err, explicit_secret=secret),
                timestamp=timestamp,
            )

        # msg_type == "transcript" (final segment after server-side VAD silence)
        text = str(msg.get("text") or "").strip()
        segment_id = msg.get("segment_id")
        segment_index = msg.get("segment_index")

        dedup_candidates: List[str] = []
        if segment_id:
            dedup_candidates.append(f"seg:{segment_id}")
        if segment_index is not None and text:
            dedup_candidates.append(f"idx_txt:{segment_index}:{text}")
        if not dedup_candidates and text:
            dedup_candidates.append(f"txt:{text}")

        if dedup_candidates and any(k in seen_final_keys for k in dedup_candidates):
            return None

        for k in dedup_candidates:
            seen_final_keys.add(k)

        return STTTranscriptEvent(
            kind=TranscriptEventKind.TRANSCRIPT,
            text=text,
            stable_text=text,
            volatile_text="",
            is_final=True,
            segment_id=segment_id,
            segment_index=segment_index,
            audio_duration_ms=msg.get("audio_duration_ms"),
            latency_ms=msg.get("latency"),
            language_code=language_code,
            timestamp=timestamp,
        )

    @staticmethod
    def build_update_settings_frame(
        bias_list: Optional[List[str]] = None,
        bias_score: Optional[float] = None,
        substitution_enabled: Optional[bool] = None,
        substitution_map: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[str, List[Dict[str, str]]]:
        """Build a documented `update_settings` JSON frame (`substitution` and/or `bias` only)."""
        frame: Dict[str, Any] = {"type": "update_settings"}
        dropped: List[Dict[str, str]] = []

        if substitution_enabled is not None or substitution_map is not None:
            sub_obj: Dict[str, Any] = {}
            if substitution_enabled is not None:
                sub_obj["enabled"] = bool(substitution_enabled)
            if substitution_map is not None:
                sub_obj["map"] = list(substitution_map[:10])
            frame["substitution"] = sub_obj

        if bias_list is not None or bias_score is not None:
            bias_obj: Dict[str, Any] = {}
            if bias_list is not None:
                clean_words, dropped = sanitize_bias_list(bias_list)
                bias_obj["list"] = clean_words
            if bias_score is not None and bias_score in SUPPORTED_BIAS_SCORES:
                bias_obj["score"] = float(bias_score)
            frame["bias"] = bias_obj

        return json.dumps(frame, ensure_ascii=True), dropped

    # ------------------------------------------------------------------
    # Realtime WebSocket Streaming (`WSS /stt/v3/stream`)
    # ------------------------------------------------------------------

    async def stream_pcm(
        self,
        pcm_frames: AsyncIterator[bytes],
        language_code: str = "en-IN",
        sample_rate: int = 16000,
        output_format: Optional[str] = None,
        vad_threshold: Optional[float] = None,
        min_silence_ms: Optional[int] = None,
        min_speech_ms: Optional[int] = None,
        min_segment_ms: Optional[int] = None,
        max_speech_s: Optional[float] = None,
        bias_list: Optional[List[str]] = None,
        bias_score: float = 0.0,
        realtime_pace: bool = True,
        itn_native_numerals: bool = False,
        enable_substitution: bool = False,
        substitution_map: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[STTTranscriptEvent]:
        """Stream 16-bit mono PCM frames to `WSS /stt/v3/stream` and yield normalized events."""
        if not self.circuit_breaker.allow_request():
            raise VoiceProviderError(
                code="STT_CIRCUIT_OPEN",
                message="Gnani STT circuit breaker is OPEN.",
            )

        secret = self.config.get_secret_key()
        lang = self._validate_language(language_code)
        rate = self._validate_sample_rate(sample_rate)

        clean_words: List[str] = []
        if bias_list and bias_score in SUPPORTED_BIAS_SCORES and bias_score > 0.0:
            clean_words, dropped = sanitize_bias_list(bias_list)
            self.last_dropped_bias_entries = dropped

        effective_format = resolve_stt_output_format(
            language_code=lang,
            requested_format=output_format,
            has_bias_list=bool(clean_words),
        )

        eff_vad_threshold = (
            vad_threshold if vad_threshold is not None else self.config.vad_threshold
        )
        eff_min_silence = (
            min_silence_ms if min_silence_ms is not None else self.config.vad_min_silence_ms
        )
        eff_min_speech = (
            min_speech_ms if min_speech_ms is not None else self.config.vad_min_speech_ms
        )
        eff_min_segment = (
            min_segment_ms if min_segment_ms is not None else self.config.vad_min_segment_ms
        )
        eff_max_speech = max_speech_s if max_speech_s is not None else self.config.vad_max_speech_s

        headers: Dict[str, str] = {
            "x-api-key-id": secret,
            "lang_code": lang,
            "x-sample-rate": str(rate),
            "x-format": effective_format,
            "itn_native_numerals": (
                "true" if (itn_native_numerals and effective_format == "transcribe") else "false"
            ),
            "x-vad-threshold": str(max(0.0, min(1.0, float(eff_vad_threshold)))),
            "x-min-silence-ms": str(max(100, min(2000, int(eff_min_silence)))),
            "x-min-speech-ms": str(max(50, min(2000, int(eff_min_speech)))),
            "x-min-segment-ms": str(max(100, min(1000, int(eff_min_segment)))),
        }
        if eff_max_speech is not None:
            headers["x-max-speech-s"] = str(max(5.0, min(120.0, float(eff_max_speech))))

        if enable_substitution and substitution_map:
            headers["x-enable-substitution"] = "true"
            headers["x-substitution-map"] = json.dumps(substitution_map[:10], ensure_ascii=True)

        if clean_words:
            headers["x-bias-list"] = json.dumps(clean_words, ensure_ascii=True)
            headers["x-bias-score"] = str(bias_score)

        ws_url = f"{self.config.ws_base_url}/stt/v3/stream"
        seen_final_keys: Set[str] = set()
        ws = None
        sender_task: Optional[asyncio.Task] = None
        sent_bytes_total = 0
        frame_interval_s = float(PCM_SAMPLES_PER_FRAME) / float(rate)  # 0.032s@16kHz, 0.064s@8kHz

        try:
            try:
                ws = await asyncio.wait_for(
                    self._ws_connector(
                        ws_url,
                        additional_headers=headers,
                        open_timeout=self.config.connect_timeout_seconds,
                    ),
                    timeout=self.config.connect_timeout_seconds,
                )
            except asyncio.TimeoutError as e:
                self.circuit_breaker.record_failure()
                conn_to = self.config.connect_timeout_seconds
                raise VoiceTimeoutError(
                    code="STT_WS_CONNECT_TIMEOUT",
                    message=f"Timed out connecting to Gnani STT WebSocket after {conn_to}s.",
                    explicit_secret=secret,
                ) from e
            except Exception as e:
                err_str = str(e)
                if "401" in err_str or "403" in err_str:
                    raise VoiceAuthError(
                        code="STT_WS_AUTH_FAILED",
                        message=f"Gnani STT WebSocket authentication rejected: {err_str}",
                        status_code=401 if "401" in err_str else 403,
                        explicit_secret=secret,
                    ) from e
                if "429" in err_str:
                    raise VoiceRateLimitError(
                        code="STT_WS_RATE_LIMITED",
                        message=f"Gnani STT WebSocket rate limited (429): {err_str}",
                        status_code=429,
                        explicit_secret=secret,
                    ) from e
                self.circuit_breaker.record_failure()
                raise VoiceConnectionError(
                    code="STT_WS_CONNECT_FAILED",
                    message=f"Could not connect to Gnani STT WebSocket: {err_str}",
                    explicit_secret=secret,
                ) from e

            async def _send_paced_frame(frame_bytes: bytes, frame_idx: int, origin: float) -> None:
                if realtime_pace and frame_idx > 0:
                    target_time = origin + (frame_idx * frame_interval_s)
                    delay = target_time - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                await ws.send(frame_bytes)

            async def _pump_audio() -> None:
                nonlocal sent_bytes_total
                pending = bytearray()
                frames_sent = 0
                pacing_origin = time.monotonic()

                async for chunk in pcm_frames:
                    if not chunk:
                        continue
                    sent_bytes_total += len(chunk)
                    if sent_bytes_total > self.config.max_audio_bytes:
                        max_b = self.config.max_audio_bytes
                        raise VoiceInvalidAudioError(
                            code="STT_STREAM_AUDIO_TOO_LARGE",
                            message=f"Streamed audio exceeded max_audio_bytes ({max_b}).",
                        )
                    pending.extend(chunk)
                    while len(pending) >= PCM_FRAME_BYTES:
                        if frames_sent == 0:
                            pacing_origin = time.monotonic()
                        frame = bytes(pending[:PCM_FRAME_BYTES])
                        del pending[:PCM_FRAME_BYTES]
                        await _send_paced_frame(frame, frames_sent, pacing_origin)
                        frames_sent += 1
                if pending:
                    if frames_sent == 0:
                        pacing_origin = time.monotonic()
                    # Pad final partial frame with silence to exact 1,024-byte boundary
                    padded = bytes(pending) + b"\x00" * (PCM_FRAME_BYTES - len(pending))
                    await _send_paced_frame(padded, frames_sent, pacing_origin)
                    frames_sent += 1
                if sent_bytes_total == 0:
                    raise VoiceInvalidAudioError(
                        code="STT_EMPTY_AUDIO",
                        message="Audio stream yielded 0 bytes of PCM audio.",
                    )

            sender_task = asyncio.create_task(_pump_audio())
            start_time = time.monotonic()
            messages_count = 0
            received_transcript_after_send = False

            while True:
                elapsed = time.monotonic() - start_time
                remaining_overall = self.config.stream_timeout_seconds - elapsed
                if remaining_overall <= 0:
                    s_to = self.config.stream_timeout_seconds
                    raise VoiceTimeoutError(
                        code="STT_STREAM_TIMEOUT",
                        message=f"Gnani STT stream exceeded overall timeout of {s_to}s.",
                        explicit_secret=secret,
                    )

                try:
                    if sender_task.done():
                        drain_wait = 0.15 if received_transcript_after_send else 0.8
                        recv_timeout = min(
                            drain_wait,
                            self.config.request_timeout_seconds,
                            remaining_overall,
                        )
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
                    else:
                        step_timeout = min(self.config.request_timeout_seconds, remaining_overall)
                        recv_fut = asyncio.ensure_future(ws.recv())
                        done_set, _ = await asyncio.wait(
                            [recv_fut, sender_task],
                            timeout=step_timeout,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if recv_fut in done_set:
                            raw_msg = recv_fut.result()
                        elif sender_task in done_set:
                            try:
                                raw_msg = await asyncio.wait_for(recv_fut, timeout=0.8)
                            except (asyncio.TimeoutError, Exception):
                                if not recv_fut.done():
                                    recv_fut.cancel()
                                    with contextlib.suppress(asyncio.CancelledError, Exception):
                                        await recv_fut
                                raise
                        else:
                            recv_fut.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await recv_fut
                            raise asyncio.TimeoutError()
                except asyncio.TimeoutError as e:
                    if sender_task.done():
                        # Propagate any sender error (e.g., empty audio) before breaking
                        sender_exc = sender_task.exception()
                        if sender_exc is not None:
                            raise sender_exc from None
                        break
                    raise VoiceTimeoutError(
                        code="STT_RECV_TIMEOUT",
                        message=(
                            f"Timed out waiting for Gnani STT WebSocket frame "
                            f"after {self.config.request_timeout_seconds:.1f}s."
                        ),
                        explicit_secret=secret,
                    ) from e
                except websockets.ConnectionClosed:
                    if not sender_task.done():
                        await sender_task
                    if not sender_task.cancelled():
                        sender_exc = sender_task.exception()
                        if sender_exc is not None:
                            raise sender_exc from None
                    break

                messages_count += 1
                if messages_count > self.config.max_ws_messages:
                    max_m = self.config.max_ws_messages
                    raise VoiceProviderError(
                        code="STT_MAX_WS_MESSAGES_EXCEEDED",
                        message=f"STT WebSocket exceeded max_ws_messages ({max_m}).",
                    )

                if isinstance(raw_msg, bytes):
                    continue

                event = self.normalize_ws_message(
                    raw_message=raw_msg,
                    seen_final_keys=seen_final_keys,
                    language_code=lang,
                    secret=secret,
                )
                if event is not None:
                    if event.kind == TranscriptEventKind.FINAL:
                        self._operationally_tested = True
                        self.circuit_breaker.record_success()
                        if sender_task.done():
                            received_transcript_after_send = True
                    yield event

            # Ensure sender finished and surface any sender exception or empty-stream condition
            if not sender_task.done():
                await sender_task
            if not sender_task.cancelled():
                exc = sender_task.exception()
                if exc is not None:
                    raise exc
            if sent_bytes_total == 0:
                raise VoiceInvalidAudioError(
                    code="STT_EMPTY_AUDIO",
                    message="Audio stream yielded 0 bytes of PCM audio.",
                )

        finally:
            if sender_task is not None and not sender_task.done():
                sender_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await sender_task
            if ws is not None:
                with contextlib.suppress(Exception):
                    await ws.close()
