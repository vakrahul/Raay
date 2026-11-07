"""Shared contracts, audio container utilities, configuration, and sanitized errors (Milestone 1).

Enforces:
- Strict credential isolation (SecretStr / redacted representations; never serialized or logged).
- Typed STT and TTS contracts matching verified Gnani Prisma v2.5 and Timbre v2.5/v3.0 protocols.
- Chunk-walking RIFF/WAVE parser that validates container structure and repairs streaming
  0xFFFFFFFF sentinel lengths without blind 44-byte overwrites.
"""

import os
import struct
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from src.orchestrator.errors import ProviderError
from src.security.audit_logger import redact_secrets
from src.storage.credential_vault import CredentialVault

# ---------------------------------------------------------------------------
# Verified Gnani Protocol Constants
# ---------------------------------------------------------------------------

GNANI_REST_BASE_URL = "https://api.vachana.ai"
GNANI_WS_BASE_URL = "wss://api.vachana.ai"

SUPPORTED_STT_LANGUAGES: Set[str] = {
    "bn-IN",
    "en-IN",
    "gu-IN",
    "hi-IN",
    "kn-IN",
    "ml-IN",
    "mr-IN",
    "pa-IN",
    "ta-IN",
    "te-IN",
}

SUPPORTED_TTS_LANGUAGES: Set[str] = SUPPORTED_STT_LANGUAGES | {"auto", "hi-en"}

SUPPORTED_STT_SAMPLE_RATES: Set[int] = {8000, 16000, 44100, 48000}
SUPPORTED_TTS_SAMPLE_RATES: Set[int] = {8000, 16000, 22050, 24000, 44100, 48000}
SUPPORTED_BIAS_SCORES: Set[float] = {0.0, 0.5, 1.0, 1.5, 2.0}
ITN_SUPPORTED_LANGUAGES: Set[str] = {"hi-IN", "en-IN"}

TIMBRE_V25_VOICES: Set[str] = {
    "Ambuja",
    "Asmita",
    "Bhavna",
    "Brinda",
    "Chitra",
    "Deepak",
    "Devika",
    "Dhruva",
    "Falak",
    "Girish",
    "Hemraj",
    "Ishaan",
    "Jalaj",
    "Jwala",
    "Kaveri",
    "Kavin",
    "Kirra",
    "Lavanya",
    "Lehara",
    "Mehuli",
    "Nalini",
    "Noopur",
    "Omkar",
    "Poorvi",
    "Pranav",
    "Reshma",
    "Riyaan",
    "Roopesh",
    "Saanvi",
    "Shlok",
    "Suhana",
    "Trisha",
    "Trupti",
    "Urmila",
    "Varuni",
    "Vedika",
    "Veera",
    "Vikrant",
    "Yashvi",
    "Yukti",
    "Zahira",
    "Zayan",
}

TIMBRE_V30_VOICES: Set[str] = {
    "Avinash",
    "Chetana",
    "Devansh",
    "Harshad",
    "Kalpana",
    "Lakshmi",
    "Prakash",
    "Riyaa",
    "Shivani",
    "Shubham",
    "Tarika",
    "Vasudha",
    "Yashraj",
}

WAV_SENTINEL_SIZE = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Sanitized Voice Error Hierarchy
# ---------------------------------------------------------------------------


def sanitize_error_message(message: str, explicit_secret: Optional[str] = None) -> str:
    """Scrub any secret tokens, headers, or explicit credential strings from error text."""
    text = str(message or "")
    if explicit_secret and len(explicit_secret) >= 4:
        text = text.replace(explicit_secret, "[REDACTED_SECRET]")
    return str(redact_secrets(text))


class VoiceProviderError(ProviderError):
    """Base sanitized error for STT and TTS provider operations."""

    def __init__(
        self,
        code: str,
        message: str,
        remediation: str = "",
        status_code: Optional[int] = None,
        explicit_secret: Optional[str] = None,
    ):
        clean_msg = sanitize_error_message(message, explicit_secret=explicit_secret)
        clean_rem = sanitize_error_message(remediation, explicit_secret=explicit_secret)
        super().__init__(code=code, message=clean_msg, remediation=clean_rem)
        self.status_code = status_code


class VoiceConfigError(VoiceProviderError):
    """Raised when required configuration or API key is missing or invalid."""


class VoiceAuthError(VoiceProviderError):
    """Raised on HTTP/WS 401 Unauthorized or 403 Forbidden responses."""


class VoiceRateLimitError(VoiceProviderError):
    """Raised on HTTP 429 Too Many Requests responses."""


class VoiceTimeoutError(VoiceProviderError):
    """Raised when connection, read, or overall operation timeout expires."""


class VoiceInvalidAudioError(VoiceProviderError):
    """Raised when input or output audio is empty, oversized, or structurally invalid."""


class VoiceConnectionError(VoiceProviderError):
    """Raised on network or WebSocket transport failures."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class GnaniConfig(BaseModel):
    """Validated, secret-safe configuration for Gnani Prisma STT and Timbre TTS."""

    model_config = ConfigDict(extra="forbid")

    api_key: Optional[SecretStr] = Field(default=None, repr=False)
    rest_base_url: str = Field(default=GNANI_REST_BASE_URL)
    ws_base_url: str = Field(default=GNANI_WS_BASE_URL)
    connect_timeout_seconds: float = Field(default=10.0, ge=0.5, le=60.0)
    request_timeout_seconds: float = Field(default=25.0, ge=1.0, le=120.0)
    stream_timeout_seconds: float = Field(default=60.0, ge=1.0, le=900.0)
    max_audio_bytes: int = Field(default=15 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_tts_text_chars: int = Field(default=5000, ge=1, le=20000)
    max_tts_response_bytes: int = Field(default=25 * 1024 * 1024, ge=4096, le=100 * 1024 * 1024)
    max_ws_messages: int = Field(default=2000, ge=10, le=50000)
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    vad_min_silence_ms: int = Field(default=350, ge=100, le=2000)
    vad_min_speech_ms: int = Field(default=100, ge=50, le=2000)
    vad_min_segment_ms: int = Field(default=120, ge=100, le=1000)
    vad_max_speech_s: Optional[float] = Field(default=None, ge=5.0, le=120.0)
    segment_stitch_window_ms: int = Field(default=650, ge=50, le=5000)
    reconnect_on_profile_switch: bool = Field(default=False)

    @field_validator("rest_base_url", "ws_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        cleaned = (v or "").strip().rstrip("/")
        if not cleaned:
            raise ValueError("Base URL cannot be empty")
        return cleaned

    @classmethod
    def from_env_or_vault(
        cls,
        api_key: Optional[str] = None,
        vault: Optional[CredentialVault] = None,
        allow_vault_lookup: bool = True,
        **overrides: Any,
    ) -> "GnaniConfig":
        """Resolve Gnani API key from explicit arg, environment variable, or DPAPI vault."""
        resolved_key = (api_key or "").strip()
        if not resolved_key:
            resolved_key = os.environ.get("GNANI_API_KEY", "").strip()
        if not resolved_key:
            resolved_key = os.environ.get("VACHANA_API_KEY", "").strip()
        if not resolved_key and allow_vault_lookup:
            try:
                active_vault = vault or CredentialVault()
                resolved_key = (active_vault.get_credential("gnani") or "").strip()
            except Exception:
                resolved_key = ""

        secret_val = SecretStr(resolved_key) if resolved_key else None
        return cls(api_key=secret_val, **overrides)

    @property
    def is_configured(self) -> bool:
        """Return True when a non-empty API key is present."""
        return bool(self.api_key and self.api_key.get_secret_value().strip())

    def get_secret_key(self) -> str:
        """Return raw secret key for internal header construction only; raise if missing."""
        if not self.is_configured or self.api_key is None:
            raise VoiceConfigError(
                code="VOICE_KEY_MISSING",
                message="Gnani API key is not configured.",
                remediation=(
                    "Configure GNANI_API_KEY in the environment or "
                    "Windows DPAPI CredentialVault('gnani')."
                ),
            )
        return self.api_key.get_secret_value().strip()

    def public_summary(self) -> Dict[str, Any]:
        """Return secret-free configuration metadata."""
        return {
            "configured": self.is_configured,
            "rest_base_url": self.rest_base_url,
            "ws_base_url": self.ws_base_url,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "stream_timeout_seconds": self.stream_timeout_seconds,
            "max_audio_bytes": self.max_audio_bytes,
            "max_tts_text_chars": self.max_tts_text_chars,
            "vad_threshold": self.vad_threshold,
            "vad_min_silence_ms": self.vad_min_silence_ms,
            "vad_min_speech_ms": self.vad_min_speech_ms,
            "vad_min_segment_ms": self.vad_min_segment_ms,
            "segment_stitch_window_ms": self.segment_stitch_window_ms,
            "reconnect_on_profile_switch": self.reconnect_on_profile_switch,
        }


# ---------------------------------------------------------------------------
# STT Models & Contracts (Exact 5 Documented Server Message Types)
# ---------------------------------------------------------------------------


class TranscriptEventKind(str, Enum):
    CONNECTED = "connected"
    PROCESSING = "processing"
    TRANSCRIPT = "transcript"
    FINAL = "transcript"
    SETTINGS_UPDATED = "settings_updated"
    ERROR = "error"


class STTTranscriptEvent(BaseModel):
    """Normalized event emitted by GnaniSTTProvider during streaming or REST transcription."""

    model_config = ConfigDict(extra="forbid")

    kind: TranscriptEventKind
    text: str = ""
    stable_text: str = ""
    volatile_text: str = ""
    literal_text: Optional[str] = None
    is_final: bool = False
    utterance_id: Optional[str] = None
    segment_id: Optional[str] = None
    segment_index: Optional[int] = None
    sequence: Optional[int] = None
    audio_duration_ms: Optional[float] = None
    latency_ms: Optional[float] = None
    language_code: Optional[str] = None
    timestamp: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class STTResult(BaseModel):
    """Normalized result from a complete REST or aggregated WebSocket transcription."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    transcript: str
    literal_transcript: Optional[str] = None
    language_code: str
    model: Optional[str] = None
    request_id: Optional[str] = None
    processing_time_ms: Optional[float] = None
    end_to_end_latency_ms: Optional[float] = None
    segments: List[STTTranscriptEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TTS Models & Contracts
# ---------------------------------------------------------------------------


class TTSAudioConfig(BaseModel):
    """Validated Timbre audio output configuration."""

    model_config = ConfigDict(extra="forbid")

    sample_rate: int = Field(default=24000)
    num_channels: int = Field(default=1, ge=1, le=2)
    sample_width: int = Field(default=2, ge=2, le=2)
    encoding: str = Field(
        default="linear_pcm", pattern="^(linear_pcm|pcm_s16le|pcm_mulaw|pcm_alaw|oggopus)$"
    )
    container: str = Field(default="wav", pattern="^(wav|raw|mp3|ogg|mulaw|alaw)$")
    bitrate: Optional[str] = Field(default=None, pattern="^(32k|64k|96k|128k|192k)$")

    @field_validator("sample_rate")
    @classmethod
    def _validate_sample_rate(cls, v: int) -> int:
        if v not in SUPPORTED_TTS_SAMPLE_RATES:
            allowed = sorted(SUPPORTED_TTS_SAMPLE_RATES)
            raise ValueError(f"Unsupported Timbre sample_rate {v}. Must be one of {allowed}")
        return v


class TTSRequest(BaseModel):
    """Validated Timbre TTS synthesis request."""

    model_config = ConfigDict(extra="forbid")

    text: str
    voice: str = Field(default="Shivani")
    model: str = Field(default="timbre-v3.0", pattern="^(timbre-v2\\.5|timbre-v3\\.0)$")
    language: str = Field(default="auto")
    speed: float = Field(default=1.0, ge=0.85, le=1.15)
    audio_config: TTSAudioConfig = Field(default_factory=TTSAudioConfig)

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        if v not in SUPPORTED_TTS_LANGUAGES:
            raise ValueError(
                f"Unsupported Timbre language '{v}'. Supported: {sorted(SUPPORTED_TTS_LANGUAGES)}"
            )
        return v


class WavHeaderMetadata(BaseModel):
    """Parsed metadata from a RIFF WAVE container."""

    model_config = ConfigDict(extra="forbid")

    audio_format: int
    num_channels: int
    sample_rate: int
    byte_rate: int
    block_align: int
    bits_per_sample: int
    riff_size_field: int
    data_size_field: int
    data_chunk_offset: int
    data_payload_offset: int
    actual_data_bytes: int
    had_sentinel_size: bool
    duration_seconds: Optional[float] = None


class TTSAudioResult(BaseModel):
    """Typed output contract for synthesized speech audio."""

    model_config = ConfigDict(extra="forbid")

    audio_bytes: bytes = Field(repr=False)
    container: str
    mime_type: str
    encoding: str
    sample_rate: int
    num_channels: int
    sample_width_bytes: int
    duration_seconds: Optional[float] = None
    model: str
    voice: str
    language: str
    wav_header_repaired: bool = False
    chunks_count: int = 1
    latency_ms: float = 0.0

    @property
    def size_bytes(self) -> int:
        return len(self.audio_bytes)


class TTSStreamChunk(BaseModel):
    """Single audio fragment from Timbre SSE streaming."""

    model_config = ConfigDict(extra="forbid")

    chunk_index: int
    audio_bytes: bytes = Field(repr=False)
    is_final: bool = False


# ---------------------------------------------------------------------------
# RIFF / WAV Container Inspection & Normalization Utilities
# ---------------------------------------------------------------------------


def parse_wav_container(data: bytes) -> WavHeaderMetadata:
    """Parse a RIFF WAVE container by walking subchunks rather than assuming a fixed 44-byte header.

    Raises VoiceInvalidAudioError if the bytes are not a valid RIFF WAVE container
    or lack required 'fmt ' and 'data' subchunks.
    """
    if not data or len(data) < 44:
        size_len = len(data) if data else 0
        raise VoiceInvalidAudioError(
            code="WAV_TOO_SHORT",
            message=f"WAV container is too short ({size_len} bytes; minimum 44 bytes required).",
        )
    if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise VoiceInvalidAudioError(
            code="WAV_INVALID_SIGNATURE",
            message="Audio bytes do not start with a valid RIFF/WAVE header.",
        )

    riff_size_field = struct.unpack("<I", data[4:8])[0]
    offset = 12
    total_len = len(data)

    fmt_info: Optional[Tuple[int, int, int, int, int, int]] = None
    data_chunk_offset: Optional[int] = None
    data_size_field: Optional[int] = None
    data_payload_offset: Optional[int] = None

    while offset + 8 <= total_len:
        chunk_id = data[offset : offset + 4]
        chunk_size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        payload_start = offset + 8

        if chunk_id == b"fmt ":
            if payload_start + 16 > total_len:
                raise VoiceInvalidAudioError(
                    code="WAV_TRUNCATED_FMT",
                    message="WAV 'fmt ' chunk is truncated.",
                )
            audio_fmt, channels, sample_rate, byte_rate, block_align, bits_per_sample = (
                struct.unpack("<HHIIHH", data[payload_start : payload_start + 16])
            )
            if channels <= 0 or sample_rate <= 0 or bits_per_sample <= 0:
                raise VoiceInvalidAudioError(
                    code="WAV_INVALID_FMT",
                    message=(
                        f"WAV 'fmt ' chunk has invalid parameters "
                        f"(channels={channels}, rate={sample_rate}, bits={bits_per_sample})."
                    ),
                )
            fmt_info = (audio_fmt, channels, sample_rate, byte_rate, block_align, bits_per_sample)
            # Advance to next chunk (word-aligned)
            advance = chunk_size + (chunk_size % 2)
            offset = payload_start + advance

        elif chunk_id == b"data":
            data_chunk_offset = offset
            data_size_field = chunk_size
            data_payload_offset = payload_start
            break

        else:
            # Skip non-data subchunk (e.g. JUNK, LIST, fact, INFO)
            if chunk_size == WAV_SENTINEL_SIZE or payload_start + chunk_size > total_len:
                raise VoiceInvalidAudioError(
                    code="WAV_CORRUPT_SUBCHUNK",
                    message=f"WAV subchunk {chunk_id!r} has invalid size {chunk_size}.",
                )
            advance = chunk_size + (chunk_size % 2)
            offset = payload_start + advance

    if fmt_info is None:
        raise VoiceInvalidAudioError(
            code="WAV_MISSING_FMT",
            message="WAV container is missing the required 'fmt ' subchunk.",
        )
    if data_chunk_offset is None or data_payload_offset is None or data_size_field is None:
        raise VoiceInvalidAudioError(
            code="WAV_MISSING_DATA",
            message="WAV container is missing the required 'data' subchunk.",
        )

    audio_fmt, channels, sample_rate, byte_rate, block_align, bits_per_sample = fmt_info
    available_payload = total_len - data_payload_offset
    if available_payload <= 0:
        raise VoiceInvalidAudioError(
            code="WAV_EMPTY_DATA",
            message="WAV container 'data' subchunk contains 0 audio bytes.",
        )

    had_sentinel = (riff_size_field == WAV_SENTINEL_SIZE) or (data_size_field == WAV_SENTINEL_SIZE)
    actual_data_bytes = (
        available_payload
        if (had_sentinel or data_size_field > available_payload)
        else data_size_field
    )

    bytes_per_second = sample_rate * channels * max(1, bits_per_sample // 8)
    duration = (
        round(actual_data_bytes / float(bytes_per_second), 4) if bytes_per_second > 0 else None
    )

    return WavHeaderMetadata(
        audio_format=audio_fmt,
        num_channels=channels,
        sample_rate=sample_rate,
        byte_rate=byte_rate,
        block_align=block_align,
        bits_per_sample=bits_per_sample,
        riff_size_field=riff_size_field,
        data_size_field=data_size_field,
        data_chunk_offset=data_chunk_offset,
        data_payload_offset=data_payload_offset,
        actual_data_bytes=actual_data_bytes,
        had_sentinel_size=had_sentinel,
        duration_seconds=duration,
    )


def normalize_wav_bytes(data: bytes) -> Tuple[bytes, WavHeaderMetadata, bool]:
    """Validate WAV container and repair 0xFFFFFFFF streaming sentinel or mismatched size fields.

    Returns (normalized_wav_bytes, updated_metadata, was_repaired).
    """
    meta = parse_wav_container(data)
    expected_data_size = len(data) - meta.data_payload_offset
    expected_riff_size = len(data) - 8

    needs_repair = (
        meta.had_sentinel_size
        or meta.riff_size_field != expected_riff_size
        or meta.data_size_field != expected_data_size
    )
    if not needs_repair:
        return data, meta, False

    buf = bytearray(data)
    struct.pack_into("<I", buf, 4, expected_riff_size)
    struct.pack_into("<I", buf, meta.data_chunk_offset + 4, expected_data_size)
    repaired_bytes = bytes(buf)
    repaired_meta = parse_wav_container(repaired_bytes)
    return repaired_bytes, repaired_meta, True


def build_wav_from_pcm(
    pcm_bytes: bytes,
    sample_rate: int,
    num_channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Construct a valid RIFF WAVE file around raw signed little-endian PCM bytes."""
    if not pcm_bytes:
        raise VoiceInvalidAudioError(
            code="PCM_EMPTY",
            message="Cannot construct WAV from empty PCM payload.",
        )
    if sample_rate <= 0 or num_channels <= 0 or sample_width not in (1, 2, 4):
        raise VoiceInvalidAudioError(
            code="PCM_INVALID_PARAMS",
            message=(
                f"Invalid PCM parameters: rate={sample_rate}, "
                f"channels={num_channels}, width={sample_width}."
            ),
        )

    data_size = len(pcm_bytes)
    riff_size = 36 + data_size
    byte_rate = sample_rate * num_channels * sample_width
    block_align = num_channels * sample_width
    bits_per_sample = sample_width * 8

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM format = 1
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_bytes


# ---------------------------------------------------------------------------
# Base STT & TTS Provider Interfaces
# ---------------------------------------------------------------------------


class BaseSTTProvider(ABC):
    """Abstract base interface for Speech-to-Text providers."""

    @abstractmethod
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
        """Transcribe bounded audio bytes via synchronous request."""

    @abstractmethod
    def stream_pcm(
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
        """Stream PCM audio frames over WebSocket and yield normalized STT events."""

    @abstractmethod
    async def health_check(self) -> Tuple[bool, str]:
        """Perform non-destructive reachability/auth probe."""


class BaseTTSProvider(ABC):
    """Abstract base interface for Text-to-Speech providers."""

    @abstractmethod
    async def synthesize(self, request: TTSRequest) -> TTSAudioResult:
        """Synthesize complete audio from a validated TTSRequest."""

    @abstractmethod
    def synthesize_stream(self, request: TTSRequest) -> AsyncIterator[TTSStreamChunk]:
        """Stream synthesized audio chunks via SSE."""

    @abstractmethod
    async def health_check(self) -> Tuple[bool, str]:
        """Perform non-destructive reachability/auth probe."""
