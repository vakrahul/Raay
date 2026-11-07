"""Typed audio models, state machines, RMS utilities, and sanitized audio errors (Milestone 2)."""

import math
import struct
import time
from enum import Enum
from typing import Optional, Set

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.voice.base import (
    SUPPORTED_STT_SAMPLE_RATES,
    SUPPORTED_TTS_SAMPLE_RATES,
    VoiceProviderError,
)

ALL_SUPPORTED_AUDIO_RATES: Set[int] = SUPPORTED_STT_SAMPLE_RATES | SUPPORTED_TTS_SAMPLE_RATES


# ---------------------------------------------------------------------------
# Explicit State Models
# ---------------------------------------------------------------------------


class CaptureState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RECORDING = "RECORDING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class PlaybackState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    PLAYING = "PLAYING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class VoiceSessionState(str, Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    CAPTURING = "CAPTURING"
    TRANSCRIBING = "TRANSCRIBING"
    SPEAKING = "SPEAKING"
    STOPPING = "STOPPING"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


class QueueOverflowPolicy(str, Enum):
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    RAISE_ERROR = "raise_error"


class AudioDeviceKind(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


# ---------------------------------------------------------------------------
# Sanitized Audio & Device Errors
# ---------------------------------------------------------------------------


class AudioDeviceError(VoiceProviderError):
    """Base sanitized error for local microphone and speaker operations."""


class AudioDeviceUnavailableError(AudioDeviceError):
    """Raised when no audio device exists or a device disconnects mid-session."""


class AudioPermissionError(AudioDeviceError):
    """Raised when the OS denies access to a microphone or speaker device."""


class AudioStateError(AudioDeviceError):
    """Raised on an invalid capture, playback, or session state transition."""


class AudioFormatError(AudioDeviceError):
    """Raised when an audio format, codec, or PCM alignment is invalid or unsupported."""


class AudioQueueOverflowError(AudioDeviceError):
    """Raised when QueueOverflowPolicy.RAISE_ERROR is active and the buffer is full."""


# ---------------------------------------------------------------------------
# Validated Audio Configuration & Frame Models
# ---------------------------------------------------------------------------


class AudioFormatConfig(BaseModel):
    """Validated PCM capture configuration."""

    model_config = ConfigDict(extra="forbid")

    sample_rate: int = Field(default=16000)
    channels: int = Field(default=1, ge=1, le=2)
    sample_width: int = Field(default=2, ge=2, le=2)
    frame_bytes: int = Field(default=1024, ge=256, le=65536)
    max_queue_frames: int = Field(default=256, ge=4, le=4096)
    overflow_policy: QueueOverflowPolicy = Field(default=QueueOverflowPolicy.DROP_OLDEST)
    max_session_bytes: int = Field(default=15 * 1024 * 1024, ge=4096, le=50 * 1024 * 1024)

    @field_validator("sample_rate")
    @classmethod
    def _validate_sample_rate(cls, v: int) -> int:
        if v not in ALL_SUPPORTED_AUDIO_RATES:
            allowed = sorted(ALL_SUPPORTED_AUDIO_RATES)
            raise ValueError(f"Unsupported sample_rate {v}. Must be one of {allowed}.")
        return v

    @field_validator("frame_bytes")
    @classmethod
    def _validate_frame_alignment(cls, v: int) -> int:
        if v % 2 != 0:
            raise ValueError("frame_bytes must be aligned to 16-bit sample boundaries (even).")
        return v

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.channels * self.sample_width

    @property
    def frame_duration_ms(self) -> float:
        bps = self.bytes_per_second
        return round((self.frame_bytes / float(bps)) * 1000.0, 2) if bps > 0 else 0.0


class AudioDeviceInfo(BaseModel):
    """Metadata describing an enumerated input or output audio device."""

    model_config = ConfigDict(extra="forbid")

    device_id: int
    name: str
    kind: AudioDeviceKind
    max_channels: int = Field(default=1, ge=1)
    is_default: bool = False


class AudioFrame(BaseModel):
    """Single captured PCM audio frame with measured RMS energy and sequence metadata."""

    model_config = ConfigDict(extra="forbid")

    pcm_bytes: bytes = Field(repr=False)
    sequence: int
    sample_rate: int
    channels: int
    sample_width: int
    rms: float = Field(ge=0.0, le=1.0)
    duration_ms: float
    timestamp: float = Field(default_factory=time.time)

    @property
    def size_bytes(self) -> int:
        return len(self.pcm_bytes)


class PlaybackOutcome(BaseModel):
    """Result returned when speaker playback finishes or is cancelled."""

    model_config = ConfigDict(extra="forbid")

    completed: bool
    cancelled: bool
    bytes_played: int
    duration_played_ms: float
    device_id: Optional[int] = None


def compute_pcm_rms(pcm_bytes: bytes, sample_width: int = 2) -> float:
    """Compute normalized RMS amplitude (0.0 to 1.0) from signed 16-bit little-endian PCM."""
    if not pcm_bytes or sample_width != 2 or len(pcm_bytes) < 2:
        return 0.0
    usable_len = len(pcm_bytes) - (len(pcm_bytes) % 2)
    if usable_len <= 0:
        return 0.0
    count = usable_len // 2
    samples = struct.unpack(f"<{count}h", pcm_bytes[:usable_len])
    sum_sq = sum(s * s for s in samples)
    mean_sq = sum_sq / float(count)
    rms_raw = math.sqrt(mean_sq) / 32768.0
    return round(min(1.0, max(0.0, rms_raw)), 4)


class PCM16NoiseCanceller:
    """Real-time 16-bit PCM noise cancellation (80 Hz high-pass filter + adaptive noise floor gate).

    Removes DC offset / low-frequency room & electrical hum (<80 Hz) and attenuates
    stationary background floor noise between speech segments while preserving speech
    transients and vocal formants at 1.0x unity gain.
    """

    def __init__(
        self,
        sample_rate_hz: int = 16000,
        cutoff_hz: float = 80.0,
        floor_gate_rms: float = 0.004,
        attenuation_factor: float = 0.25,
    ) -> None:
        self.sample_rate_hz = max(8000, int(sample_rate_hz))
        rc = 1.0 / (2.0 * math.pi * max(20.0, float(cutoff_hz)))
        dt = 1.0 / float(self.sample_rate_hz)
        self._hp_alpha = rc / (rc + dt)
        self.floor_gate_rms = max(0.0, min(0.05, float(floor_gate_rms)))
        self.attenuation_factor = max(0.05, min(1.0, float(attenuation_factor)))
        self._prev_x: float = 0.0
        self._prev_y: float = 0.0
        self._noise_floor_rms: float = self.floor_gate_rms

    def process_frame(self, pcm_bytes: bytes) -> bytes:
        """Filter a 16-bit signed little-endian mono PCM frame."""
        if not pcm_bytes or len(pcm_bytes) < 2:
            return pcm_bytes
        usable_len = len(pcm_bytes) - (len(pcm_bytes) % 2)
        count = usable_len // 2
        samples = struct.unpack(f"<{count}h", pcm_bytes[:usable_len])

        alpha = self._hp_alpha
        prev_x = self._prev_x
        prev_y = self._prev_y
        hp_samples = []
        sum_sq = 0.0
        for s in samples:
            x = float(s)
            y = alpha * (prev_y + x - prev_x)
            prev_x = x
            prev_y = y
            hp_samples.append(y)
            sum_sq += y * y
        self._prev_x = prev_x
        self._prev_y = prev_y

        frame_rms = math.sqrt(sum_sq / float(count)) / 32768.0
        # Track stationary noise floor slowly downward, very slowly upward during low energy
        if frame_rms < self._noise_floor_rms:
            self._noise_floor_rms = 0.90 * self._noise_floor_rms + 0.10 * frame_rms
        elif frame_rms < self.floor_gate_rms * 1.5:
            self._noise_floor_rms = 0.98 * self._noise_floor_rms + 0.02 * frame_rms

        gate_thresh = max(self.floor_gate_rms, self._noise_floor_rms * 1.6)
        gain = self.attenuation_factor if frame_rms < gate_thresh else 1.0

        out_ints = [
            max(-32768, min(32767, int(round(val * gain))))
            for val in hp_samples
        ]
        filtered = struct.pack(f"<{count}h", *out_ints)
        if usable_len < len(pcm_bytes):
            return filtered + pcm_bytes[usable_len:]
        return filtered


def suppress_pcm16_noise(
    pcm_bytes: bytes,
    sample_rate_hz: int = 16000,
    frame_bytes: int = 1024,
) -> bytes:
    """Apply `PCM16NoiseCanceller` across a complete PCM16 buffer in `frame_bytes` chunks."""
    canceller = PCM16NoiseCanceller(sample_rate_hz=sample_rate_hz)
    out = bytearray()
    for offset in range(0, len(pcm_bytes), frame_bytes):
        out.extend(canceller.process_frame(pcm_bytes[offset : offset + frame_bytes]))
    return bytes(out)
