"""Gnani Timbre v2.5 & v3.0 Text-to-Speech (TTS) Provider Adapter (Milestone 1).

Supports:
- Synchronous REST synthesis (`POST /api/v1/tts/inference`).
- Server-Sent Events (SSE) progressive streaming (`POST /api/v1/tts/sse`).
- Strict model/voice catalog validation (`timbre-v2.5` vs `timbre-v3.0`).
- Chunk-walking RIFF WAVE header validation and `0xFFFFFFFF` streaming sentinel repair.
- Explicit raw PCM (`container="raw"`) and compressed (`mp3`, `ogg`) container handling.
- Bounded input text length and output audio byte limits with sanitized error reporting.
"""

import base64
import contextlib
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from src.providers.resilience import CircuitBreaker
from src.voice.base import (
    TIMBRE_V25_VOICES,
    TIMBRE_V30_VOICES,
    BaseTTSProvider,
    GnaniConfig,
    TTSAudioResult,
    TTSRequest,
    TTSStreamChunk,
    VoiceAuthError,
    VoiceConfigError,
    VoiceConnectionError,
    VoiceInvalidAudioError,
    VoiceProviderError,
    VoiceRateLimitError,
    VoiceTimeoutError,
    build_wav_from_pcm,
    normalize_wav_bytes,
    sanitize_error_message,
)

CONTAINER_MIME_TYPES: Dict[str, str] = {
    "wav": "audio/wav",
    "raw": "application/octet-stream",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "mulaw": "audio/basic",
    "alaw": "audio/alaw",
}

_ONLY_AUDIO_TAGS_RE = re.compile(r"^(?:\s*<[a-zA-Z _-]+>\s*)+$")


def compute_tts_cache_key(text: str, voice: str, model: str) -> str:
    """Compute deterministic sha256(text+voice+model) cache key for repeated phrases."""
    raw = f"{(text or '').strip()}|{voice.strip()}|{model.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GnaniTTSProvider(BaseTTSProvider):
    """Provider adapter for Gnani Timbre v2.5 and v3.0 Text-to-Speech."""

    def __init__(
        self,
        config: Optional[GnaniConfig] = None,
        http_transport: Optional[httpx.AsyncBaseTransport] = None,
        cache_dir: Optional[Path] = None,
    ):
        self.config = config or GnaniConfig.from_env_or_vault()
        self._http_transport = http_transport
        self.cache_dir: Optional[Path] = cache_dir
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        self._operationally_tested: bool = False

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    @property
    def is_operationally_tested(self) -> bool:
        return self._operationally_tested

    # ------------------------------------------------------------------
    # Request Validation & Payload Construction
    # ------------------------------------------------------------------

    def _validate_request(self, request: TTSRequest) -> Dict[str, Any]:
        clean_text = (request.text or "").strip()
        if not clean_text:
            raise VoiceConfigError(
                code="TTS_EMPTY_TEXT",
                message="TTS input text cannot be empty.",
            )
        if len(clean_text) > self.config.max_tts_text_chars:
            max_c = self.config.max_tts_text_chars
            raise VoiceConfigError(
                code="TTS_TEXT_TOO_LONG",
                message=(
                    f"TTS input text ({len(clean_text)} chars) "
                    f"exceeds max_tts_text_chars ({max_c})."
                ),
            )
        if _ONLY_AUDIO_TAGS_RE.match(clean_text):
            raise VoiceConfigError(
                code="TTS_ONLY_TAGS",
                message="TTS input text cannot consist solely of inline audio tags.",
            )

        if request.model == "timbre-v2.5" and request.voice not in TIMBRE_V25_VOICES:
            raise VoiceConfigError(
                code="TTS_VOICE_MODEL_MISMATCH",
                message=f"Voice '{request.voice}' is not in the Timbre v2.5 catalog.",
                remediation=(
                    "Use a valid timbre-v2.5 voice (e.g. Kaveri, Nalini, Suhana, "
                    "Poorvi, Saanvi) or switch model to 'timbre-v3.0'."
                ),
            )
        if request.model == "timbre-v3.0" and request.voice not in TIMBRE_V30_VOICES:
            v3_list = sorted(TIMBRE_V30_VOICES)
            raise VoiceConfigError(
                code="TTS_VOICE_MODEL_MISMATCH",
                message=f"Voice '{request.voice}' is not in the Timbre v3.0 catalog.",
                remediation=(
                    f"Use a valid timbre-v3.0 voice ({v3_list}) or switch model to 'timbre-v2.5'."
                ),
            )

        audio_cfg: Dict[str, Any] = {
            "sample_rate": request.audio_config.sample_rate,
            "num_channels": request.audio_config.num_channels,
            "sample_width": request.audio_config.sample_width,
            "encoding": request.audio_config.encoding,
            "container": request.audio_config.container,
        }
        if request.audio_config.container == "mp3" and request.audio_config.bitrate:
            audio_cfg["bitrate"] = request.audio_config.bitrate

        return {
            "text": clean_text,
            "voice": request.voice,
            "model": request.model,
            "language": request.language,
            "speed": request.speed,
            "audio_config": audio_cfg,
        }

    def _map_http_error(self, status_code: int, body_text: str, secret: str) -> VoiceProviderError:
        safe_body = sanitize_error_message(body_text[:400], explicit_secret=secret)
        if status_code == 401:
            return VoiceAuthError(
                code="TTS_AUTH_FAILED",
                message=f"Gnani TTS authentication failed (401): {safe_body}",
                remediation="Verify that GNANI_API_KEY is a valid Vachana API key.",
                status_code=401,
                explicit_secret=secret,
            )
        if status_code == 403:
            return VoiceAuthError(
                code="TTS_FORBIDDEN",
                message=f"Gnani TTS access forbidden or quota exhausted (403): {safe_body}",
                remediation="Check account permissions and credit balance on app.gnani.ai/voice.",
                status_code=403,
                explicit_secret=secret,
            )
        if status_code == 429:
            return VoiceRateLimitError(
                code="TTS_RATE_LIMITED",
                message=f"Gnani TTS rate limit exceeded (429): {safe_body}",
                remediation="Reduce synthesis request rate or wait before retrying.",
                status_code=429,
                explicit_secret=secret,
            )
        if status_code in (400, 422):
            return VoiceConfigError(
                code="TTS_BAD_REQUEST",
                message=f"Gnani TTS rejected request parameters ({status_code}): {safe_body}",
                status_code=status_code,
                explicit_secret=secret,
            )
        return VoiceProviderError(
            code=f"TTS_HTTP_{status_code}",
            message=f"Gnani TTS server error ({status_code}): {safe_body}",
            status_code=status_code,
            explicit_secret=secret,
        )

    def _finalize_audio_result(
        self,
        raw_bytes: bytes,
        request: TTSRequest,
        chunks_count: int,
        latency_ms: float,
    ) -> TTSAudioResult:
        if not raw_bytes or len(raw_bytes) == 0:
            raise VoiceInvalidAudioError(
                code="TTS_EMPTY_AUDIO",
                message="Gnani TTS returned 0 bytes of audio.",
            )
        if len(raw_bytes) > self.config.max_tts_response_bytes:
            max_b = self.config.max_tts_response_bytes
            raise VoiceInvalidAudioError(
                code="TTS_RESPONSE_TOO_LARGE",
                message=(
                    f"Gnani TTS audio payload ({len(raw_bytes)} bytes) "
                    f"exceeds max_tts_response_bytes ({max_b})."
                ),
            )

        container = request.audio_config.container
        mime_type = CONTAINER_MIME_TYPES.get(container, "application/octet-stream")

        if container == "wav":
            repaired_bytes, wav_meta, was_repaired = normalize_wav_bytes(raw_bytes)
            return TTSAudioResult(
                audio_bytes=repaired_bytes,
                container="wav",
                mime_type="audio/wav",
                encoding=request.audio_config.encoding,
                sample_rate=wav_meta.sample_rate,
                num_channels=wav_meta.num_channels,
                sample_width_bytes=max(1, wav_meta.bits_per_sample // 8),
                duration_seconds=wav_meta.duration_seconds,
                model=request.model,
                voice=request.voice,
                language=request.language,
                wav_header_repaired=was_repaired,
                chunks_count=chunks_count,
                latency_ms=latency_ms,
            )

        if container == "raw":
            sw = request.audio_config.sample_width
            if len(raw_bytes) % sw != 0:
                raise VoiceInvalidAudioError(
                    code="TTS_RAW_PCM_MISALIGNED",
                    message=(
                        f"Raw PCM byte length ({len(raw_bytes)}) is not aligned "
                        f"to sample_width ({sw})."
                    ),
                )
            bytes_per_sec = (
                request.audio_config.sample_rate
                * request.audio_config.num_channels
                * request.audio_config.sample_width
            )
            duration = (
                round(len(raw_bytes) / float(bytes_per_sec), 4) if bytes_per_sec > 0 else None
            )
            return TTSAudioResult(
                audio_bytes=raw_bytes,
                container="raw",
                mime_type="application/octet-stream",
                encoding=request.audio_config.encoding,
                sample_rate=request.audio_config.sample_rate,
                num_channels=request.audio_config.num_channels,
                sample_width_bytes=request.audio_config.sample_width,
                duration_seconds=duration,
                model=request.model,
                voice=request.voice,
                language=request.language,
                wav_header_repaired=False,
                chunks_count=chunks_count,
                latency_ms=latency_ms,
            )

        # Compressed or telephony containers (mp3, ogg, mulaw, alaw)
        effective_rate = (
            8000 if container in ("mulaw", "alaw") else request.audio_config.sample_rate
        )
        return TTSAudioResult(
            audio_bytes=raw_bytes,
            container=container,
            mime_type=mime_type,
            encoding=request.audio_config.encoding,
            sample_rate=effective_rate,
            num_channels=request.audio_config.num_channels,
            sample_width_bytes=request.audio_config.sample_width,
            duration_seconds=None,
            model=request.model,
            voice=request.voice,
            language=request.language,
            wav_header_repaired=False,
            chunks_count=chunks_count,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Non-Destructive Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> Tuple[bool, str]:
        """Verify Vachana API key validity via zero-cost GET /stt/v3/batch/jobs?limit=1."""
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
                return True, "Authenticated and reachable on Vachana API gateway."
            err = self._map_http_error(res.status_code, res.text, secret)
            return False, err.message
        except httpx.TimeoutException:
            return False, "Timed out connecting to Gnani API gateway."
        except Exception as e:
            clean_err = sanitize_error_message(str(e), explicit_secret=secret)
            return (False, f"Network error probing Gnani TTS: {clean_err}")

    # ------------------------------------------------------------------
    # Synchronous REST Synthesis (`POST /api/v1/tts/inference`)
    # ------------------------------------------------------------------

    async def synthesize(self, request: TTSRequest) -> TTSAudioResult:
        """Synthesize complete audio via POST /api/v1/tts/inference."""
        if not self.circuit_breaker.allow_request():
            raise VoiceProviderError(
                code="TTS_CIRCUIT_OPEN",
                message="Gnani TTS circuit breaker is OPEN due to consecutive upstream failures.",
            )

        secret = self.config.get_secret_key()
        payload = self._validate_request(request)
        url = f"{self.config.rest_base_url}/api/v1/tts/inference"
        t0 = time.perf_counter()

        try:
            async with httpx.AsyncClient(
                transport=self._http_transport,
                timeout=self.config.request_timeout_seconds,
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={
                        "X-API-Key-ID": secret,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response:
                    status_code = response.status_code
                    content_type = (response.headers.get("content-type") or "").lower()

                    chunks: List[bytes] = []
                    total_bytes = 0
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        total_bytes += len(chunk)
                        if total_bytes > self.config.max_tts_response_bytes:
                            max_b = self.config.max_tts_response_bytes
                            raise VoiceInvalidAudioError(
                                code="TTS_RESPONSE_TOO_LARGE",
                                message=(
                                    f"Gnani TTS response exceeded max_tts_response_bytes ({max_b})."
                                ),
                            )
                        chunks.append(chunk)
                    raw_body = b"".join(chunks)

        except VoiceProviderError:
            raise
        except httpx.TimeoutException as e:
            self.circuit_breaker.record_failure()
            req_to = self.config.request_timeout_seconds
            raise VoiceTimeoutError(
                code="TTS_TIMEOUT",
                message=f"Gnani TTS request timed out after {req_to}s.",
                explicit_secret=secret,
            ) from e
        except httpx.HTTPError as e:
            self.circuit_breaker.record_failure()
            raise VoiceConnectionError(
                code="TTS_CONNECTION_ERROR",
                message=f"Gnani TTS network error: {e}",
                explicit_secret=secret,
            ) from e

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        if status_code != 200:
            if status_code >= 500:
                self.circuit_breaker.record_failure()
            body_text = raw_body.decode("utf-8", errors="replace")
            raise self._map_http_error(status_code, body_text, secret)

        if "application/json" in content_type or "text/html" in content_type:
            preview = sanitize_error_message(
                raw_body.decode("utf-8", errors="replace")[:300], explicit_secret=secret
            )
            raise VoiceInvalidAudioError(
                code="TTS_INVALID_CONTENT_TYPE",
                message=(
                    f"Expected binary audio response but received "
                    f"Content-Type '{content_type}': {preview}"
                ),
            )

        result = self._finalize_audio_result(
            raw_bytes=raw_body,
            request=request,
            chunks_count=1,
            latency_ms=elapsed_ms,
        )
        self.circuit_breaker.record_success()
        self._operationally_tested = True
        return result

    # ------------------------------------------------------------------
    # SSE Streaming Synthesis (`POST /api/v1/tts/sse`)
    # ------------------------------------------------------------------

    async def synthesize_stream(self, request: TTSRequest) -> AsyncIterator[TTSStreamChunk]:
        """Stream base64-decoded audio chunks via Server-Sent Events (`POST /api/v1/tts/sse`)."""
        if not self.circuit_breaker.allow_request():
            raise VoiceProviderError(
                code="TTS_CIRCUIT_OPEN",
                message="Gnani TTS circuit breaker is OPEN.",
            )

        secret = self.config.get_secret_key()
        payload = self._validate_request(request)
        url = f"{self.config.rest_base_url}/api/v1/tts/sse"

        current_event = ""
        total_audio_bytes = 0
        chunks_emitted = 0
        completed_cleanly = False

        try:
            async with httpx.AsyncClient(
                transport=self._http_transport,
                timeout=self.config.stream_timeout_seconds,
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={
                        "X-API-Key-ID": secret,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        if response.status_code >= 500:
                            self.circuit_breaker.record_failure()
                        err_bytes = await response.aread()
                        raise self._map_http_error(
                            response.status_code,
                            err_bytes.decode("utf-8", errors="replace"),
                            secret,
                        )

                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        if line.startswith("event:"):
                            current_event = line.split(":", 1)[1].strip()
                            continue
                        if not line.startswith("data:"):
                            continue

                        data_str = line.split(":", 1)[1].strip()
                        try:
                            data_obj = json.loads(data_str)
                        except Exception as e:
                            raise VoiceInvalidAudioError(
                                code="TTS_SSE_MALFORMED_JSON",
                                message="Malformed JSON data frame in Gnani TTS SSE stream.",
                                explicit_secret=secret,
                            ) from e

                        if not isinstance(data_obj, dict):
                            continue

                        if current_event == "error" or "error" in data_obj:
                            err_msg = str(
                                data_obj.get("error") or data_obj.get("message") or data_obj
                            )
                            raise VoiceProviderError(
                                code="TTS_SSE_SERVER_ERROR",
                                message=f"Gnani TTS SSE error event: {err_msg}",
                                explicit_secret=secret,
                            )

                        is_final = (
                            bool(data_obj.get("is_final", False)) or current_event == "complete"
                        )
                        b64_audio = data_obj.get("audio") or ""

                        if b64_audio:
                            try:
                                audio_chunk = base64.b64decode(b64_audio, validate=True)
                            except Exception as e:
                                raise VoiceInvalidAudioError(
                                    code="TTS_SSE_INVALID_BASE64",
                                    message="Invalid base64 audio payload in Gnani TTS SSE chunk.",
                                ) from e

                            if audio_chunk:
                                total_audio_bytes += len(audio_chunk)
                                if total_audio_bytes > self.config.max_tts_response_bytes:
                                    max_b = self.config.max_tts_response_bytes
                                    raise VoiceInvalidAudioError(
                                        code="TTS_RESPONSE_TOO_LARGE",
                                        message=(
                                            f"Gnani TTS SSE stream exceeded "
                                            f"max_tts_response_bytes ({max_b})."
                                        ),
                                    )
                                chunks_emitted += 1
                                yield TTSStreamChunk(
                                    chunk_index=int(data_obj.get("chunk_index", chunks_emitted)),
                                    audio_bytes=audio_chunk,
                                    is_final=False,
                                )

                        if is_final:
                            completed_cleanly = True
                            yield TTSStreamChunk(
                                chunk_index=int(data_obj.get("chunk_index", chunks_emitted + 1)),
                                audio_bytes=b"",
                                is_final=True,
                            )
                            break

        except VoiceProviderError:
            raise
        except httpx.TimeoutException as e:
            self.circuit_breaker.record_failure()
            s_to = self.config.stream_timeout_seconds
            raise VoiceTimeoutError(
                code="TTS_SSE_TIMEOUT",
                message=f"Gnani TTS SSE stream timed out after {s_to}s.",
                explicit_secret=secret,
            ) from e
        except httpx.HTTPError as e:
            self.circuit_breaker.record_failure()
            raise VoiceConnectionError(
                code="TTS_SSE_CONNECTION_ERROR",
                message=f"Gnani TTS SSE connection error: {e}",
                explicit_secret=secret,
            ) from e

        if not completed_cleanly:
            self.circuit_breaker.record_failure()
            raise VoiceConnectionError(
                code="TTS_SSE_INTERRUPTED",
                message=(
                    "Gnani TTS SSE stream closed before a terminal 'complete' event was received."
                ),
            )

        if total_audio_bytes == 0:
            raise VoiceInvalidAudioError(
                code="TTS_EMPTY_AUDIO",
                message="Gnani TTS SSE stream completed with 0 audio bytes.",
            )

        self.circuit_breaker.record_success()
        self._operationally_tested = True

    async def synthesize_buffered_sse(
        self,
        request: TTSRequest,
        use_cache: bool = True,
    ) -> TTSAudioResult:
        """Collect all SSE stream chunks (`POST /api/v1/tts/sse`) with sha256 on-disk caching."""
        self._validate_request(request)
        cache_file: Optional[Path] = None
        if use_cache and self.cache_dir is not None and request.audio_config.container == "wav":
            key = compute_tts_cache_key(request.text, request.voice, request.model)
            cache_file = self.cache_dir / f"{key}.wav"
            if cache_file.exists():
                with contextlib.suppress(Exception):
                    cached_bytes = cache_file.read_bytes()
                    if len(cached_bytes) >= 44:
                        res = self._finalize_audio_result(
                            raw_bytes=cached_bytes,
                            request=request,
                            chunks_count=0,
                            latency_ms=0.0,
                        )
                        self.cache_hits += 1
                        self._operationally_tested = True
                        return res

        t0 = time.perf_counter()
        fragments: List[bytes] = []
        chunk_count = 0

        async for chunk in self.synthesize_stream(request):
            if chunk.audio_bytes:
                fragments.append(chunk.audio_bytes)
                chunk_count += 1

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        combined = b"".join(fragments)

        # If WAV was requested and first chunk began with a RIFF header (with 0xFFFFFFFF size),
        # normalize_wav_bytes repairs the RIFF and data chunk sizes now that full payload is known.
        # If headerless raw PCM chunks arrived when wav was requested, wrap with a WAV header.
        if request.audio_config.container == "wav" and combined and combined[:4] != b"RIFF":
            combined = build_wav_from_pcm(
                pcm_bytes=combined,
                sample_rate=request.audio_config.sample_rate,
                num_channels=request.audio_config.num_channels,
                sample_width=request.audio_config.sample_width,
            )

        res = self._finalize_audio_result(
            raw_bytes=combined,
            request=request,
            chunks_count=chunk_count,
            latency_ms=elapsed_ms,
        )
        self.cache_misses += 1

        if cache_file is not None:
            with contextlib.suppress(Exception):
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_bytes(res.audio_bytes)

        return res
