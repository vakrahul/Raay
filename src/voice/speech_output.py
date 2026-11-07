"""Controlled speaker playback manager for WinAI-OE (Milestone 2).

Reuses Milestone 1 `TTSAudioResult` and `parse_wav_container` RIFF utilities.
Guarantees:
- Validates WAV container structure or explicit raw PCM metadata before touching hardware.
- Rejects unsupported compressed codecs (`mp3`, `ogg`, `mulaw`, `alaw`) with clear errors.
- Prevents overlapping playback and supports immediate cancellation (`stop()` / `cancel()`).
- Releases the hardware output device deterministically in `finally:` on all paths.
- Zero temporary disk files created.
"""

import asyncio
import contextlib
import threading
from typing import Dict, List, Optional, Tuple, Union

from src.voice.audio_devices import BaseAudioOutputBackend, WinMMAudioOutputBackend
from src.voice.audio_types import (
    ALL_SUPPORTED_AUDIO_RATES,
    AudioDeviceError,
    AudioDeviceInfo,
    AudioDeviceUnavailableError,
    AudioFormatError,
    AudioStateError,
    PlaybackOutcome,
    PlaybackState,
)
from src.voice.base import (
    TTSAudioResult,
    VoiceInvalidAudioError,
    parse_wav_container,
)


class SpeechOutputManager:
    """Manages speaker playback of validated WAV and raw PCM audio."""

    def __init__(
        self,
        backend: Optional[BaseAudioOutputBackend] = None,
        device_id: Optional[int] = None,
        max_playback_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self._backend: BaseAudioOutputBackend = backend or WinMMAudioOutputBackend()
        self._selected_device_id: Optional[int] = device_id
        self.max_playback_bytes = max_playback_bytes

        self._state: PlaybackState = PlaybackState.IDLE
        self._state_lock = threading.Lock()
        self._cancel_event = threading.Event()

    @property
    def state(self) -> PlaybackState:
        with self._state_lock:
            return self._state

    @property
    def selected_device_id(self) -> Optional[int]:
        return self._selected_device_id

    @property
    def is_playing(self) -> bool:
        return self.state == PlaybackState.PLAYING

    # ------------------------------------------------------------------
    # Device Enumeration & Selection
    # ------------------------------------------------------------------

    def list_devices(self) -> List[AudioDeviceInfo]:
        """Enumerate available speaker output devices."""
        return self._backend.list_devices()

    def select_device(self, device_id: Optional[int]) -> None:
        """Select an output device ID (or None for default mapper) before playback."""
        if self.state in (PlaybackState.STARTING, PlaybackState.PLAYING):
            raise AudioStateError(
                code="AUDIO_OUTPUT_BUSY",
                message="Cannot change output device while playback is active.",
            )
        if device_id is not None:
            devices = self._backend.list_devices()
            valid_ids = {d.device_id for d in devices}
            if valid_ids and device_id not in valid_ids:
                raise AudioDeviceUnavailableError(
                    code="AUDIO_OUTPUT_DEVICE_NOT_FOUND",
                    message=(
                        f"Output device_id {device_id} not found in available devices "
                        f"({sorted(valid_ids)})."
                    ),
                )
        self._selected_device_id = device_id

    # ------------------------------------------------------------------
    # Audio Validation & Extraction
    # ------------------------------------------------------------------

    def _extract_validated_pcm(
        self,
        audio: Union[TTSAudioResult, bytes],
        container: Optional[str] = None,
        sample_rate: Optional[int] = None,
        channels: Optional[int] = None,
        sample_width: Optional[int] = None,
    ) -> Tuple[bytes, int, int, int]:
        """Validate audio container/metadata and return PCM bytes with format tuple."""
        if isinstance(audio, TTSAudioResult):
            raw_bytes = audio.audio_bytes
            eff_container = (container or audio.container).lower().strip()
            eff_rate = sample_rate or audio.sample_rate
            eff_channels = channels or audio.num_channels
            eff_width = sample_width or audio.sample_width_bytes
            eff_encoding = audio.encoding.lower().strip()
        elif isinstance(audio, (bytes, bytearray)):
            raw_bytes = bytes(audio)
            eff_container = (container or "").lower().strip()
            eff_rate = sample_rate or 0
            eff_channels = channels or 0
            eff_width = sample_width or 0
            eff_encoding = "linear_pcm"
        else:
            raise AudioFormatError(
                code="AUDIO_INVALID_INPUT_TYPE",
                message="play() requires a TTSAudioResult or bytes payload.",
            )

        if not raw_bytes or len(raw_bytes) == 0:
            raise AudioFormatError(
                code="AUDIO_EMPTY_PAYLOAD",
                message="Audio payload for playback is empty (0 bytes).",
            )

        if len(raw_bytes) > self.max_playback_bytes:
            raise AudioFormatError(
                code="AUDIO_PAYLOAD_TOO_LARGE",
                message=(
                    f"Audio payload ({len(raw_bytes)} bytes) exceeds "
                    f"max_playback_bytes ({self.max_playback_bytes})."
                ),
            )

        if not eff_container:
            raise AudioFormatError(
                code="AUDIO_MISSING_CONTAINER",
                message=(
                    "Raw bytes passed to play() without an explicit container type "
                    "('wav' or 'raw'). Arbitrary bytes are never assumed to be WAV or PCM."
                ),
            )

        if eff_container == "wav":
            try:
                wav_meta = parse_wav_container(raw_bytes)
            except VoiceInvalidAudioError as e:
                raise AudioFormatError(
                    code=e.code,
                    message=f"Invalid WAV container for playback: {e.message}",
                ) from e

            if wav_meta.audio_format != 1:
                raise AudioFormatError(
                    code="AUDIO_UNSUPPORTED_WAV_ENCODING",
                    message=(
                        f"WAV audio_format {wav_meta.audio_format} is not uncompressed "
                        f"linear PCM (format 1)."
                    ),
                )

            w_width = wav_meta.bits_per_sample // 8
            if wav_meta.bits_per_sample != 16 or w_width != 2:
                raise AudioFormatError(
                    code="AUDIO_UNSUPPORTED_SAMPLE_WIDTH",
                    message=(
                        f"Unsupported WAV bits_per_sample ({wav_meta.bits_per_sample}); "
                        f"16-bit PCM is required."
                    ),
                )
            if wav_meta.sample_rate not in ALL_SUPPORTED_AUDIO_RATES:
                raise AudioFormatError(
                    code="AUDIO_UNSUPPORTED_SAMPLE_RATE",
                    message=f"Unsupported WAV sample_rate ({wav_meta.sample_rate} Hz).",
                )

            pcm_end = wav_meta.data_payload_offset + wav_meta.actual_data_bytes
            pcm_payload = raw_bytes[wav_meta.data_payload_offset : pcm_end]
            block_align = wav_meta.num_channels * w_width
            if len(pcm_payload) == 0 or len(pcm_payload) % block_align != 0:
                raise AudioFormatError(
                    code="AUDIO_WAV_PCM_MISALIGNED",
                    message="WAV data payload is empty or not aligned to sample frame boundary.",
                )
            return pcm_payload, wav_meta.sample_rate, wav_meta.num_channels, w_width

        if eff_container == "raw":
            if eff_encoding not in ("linear_pcm", "pcm_s16le"):
                raise AudioFormatError(
                    code="AUDIO_UNSUPPORTED_RAW_ENCODING",
                    message=f"Unsupported raw audio encoding '{eff_encoding}'.",
                )
            if eff_rate <= 0 or eff_channels <= 0 or eff_width <= 0:
                raise AudioFormatError(
                    code="AUDIO_MISSING_PCM_METADATA",
                    message=(
                        "Raw PCM playback requires explicit sample_rate, channels, "
                        "and sample_width metadata."
                    ),
                )
            if eff_rate not in ALL_SUPPORTED_AUDIO_RATES:
                raise AudioFormatError(
                    code="AUDIO_UNSUPPORTED_SAMPLE_RATE",
                    message=f"Unsupported raw PCM sample_rate ({eff_rate} Hz).",
                )
            if eff_width != 2 or eff_channels not in (1, 2):
                raise AudioFormatError(
                    code="AUDIO_UNSUPPORTED_PCM_LAYOUT",
                    message=(
                        f"Unsupported raw PCM layout (channels={eff_channels}, "
                        f"sample_width={eff_width})."
                    ),
                )
            block_align = eff_channels * eff_width
            if len(raw_bytes) % block_align != 0:
                raise AudioFormatError(
                    code="AUDIO_RAW_PCM_MISALIGNED",
                    message=(
                        f"Raw PCM byte length ({len(raw_bytes)}) is not aligned "
                        f"to block_align ({block_align})."
                    ),
                )
            return raw_bytes, eff_rate, eff_channels, eff_width

        raise AudioFormatError(
            code="AUDIO_UNSUPPORTED_CONTAINER",
            message=(
                f"Container format '{eff_container}' is not supported by the PCM output "
                f"backend. Request 'wav' or 'raw' (linear_pcm) from GnaniTTSProvider."
            ),
        )

    # ------------------------------------------------------------------
    # Playback Lifecycle (`play`, `stop`, `cancel`, `close`)
    # ------------------------------------------------------------------

    async def play(
        self,
        audio: Union[TTSAudioResult, bytes],
        container: Optional[str] = None,
        sample_rate: Optional[int] = None,
        channels: Optional[int] = None,
        sample_width: Optional[int] = None,
    ) -> PlaybackOutcome:
        """Play validated WAV or raw PCM audio to the selected output device."""
        pcm_bytes, rate, chans, width = self._extract_validated_pcm(
            audio=audio,
            container=container,
            sample_rate=sample_rate,
            channels=channels,
            sample_width=sample_width,
        )

        with self._state_lock:
            if self._state in (PlaybackState.STARTING, PlaybackState.PLAYING):
                raise AudioStateError(
                    code="AUDIO_PLAYBACK_OVERLAPPING",
                    message="Cannot start playback while another playback is already in progress.",
                )
            self._state = PlaybackState.STARTING
            self._cancel_event.clear()

        bytes_played = 0
        try:
            try:
                await asyncio.to_thread(
                    self._backend.open_stream,
                    self._selected_device_id,
                    rate,
                    chans,
                    width,
                )
            except AudioDeviceError:
                with self._state_lock:
                    self._state = PlaybackState.ERROR
                raise
            except Exception as e:
                with self._state_lock:
                    self._state = PlaybackState.ERROR
                raise AudioDeviceError(
                    code="AUDIO_OUTPUT_OPEN_FAILED",
                    message=f"Failed to initialize speaker playback: {e}",
                ) from e

            if self._cancel_event.is_set():
                with self._state_lock:
                    self._state = PlaybackState.STOPPED
                return PlaybackOutcome(
                    completed=False,
                    cancelled=True,
                    bytes_played=0,
                    duration_played_ms=0.0,
                    device_id=self._selected_device_id,
                )

            with self._state_lock:
                self._state = PlaybackState.PLAYING

            try:
                bytes_played = await asyncio.to_thread(
                    self._backend.play_pcm,
                    pcm_bytes,
                    self._cancel_event,
                )
            except AudioDeviceError:
                with self._state_lock:
                    self._state = PlaybackState.ERROR
                raise
            except Exception as e:
                with self._state_lock:
                    self._state = PlaybackState.ERROR
                raise AudioDeviceError(
                    code="AUDIO_OUTPUT_PLAY_FAILED",
                    message=f"Speaker playback failed during output: {e}",
                ) from e

            was_cancelled = self._cancel_event.is_set() or (bytes_played < len(pcm_bytes))
            bytes_per_sec = rate * chans * width
            dur_ms = (
                round((bytes_played / float(bytes_per_sec)) * 1000.0, 2)
                if bytes_per_sec > 0
                else 0.0
            )

            with self._state_lock:
                if self._state != PlaybackState.ERROR:
                    self._state = PlaybackState.STOPPED

            return PlaybackOutcome(
                completed=(not was_cancelled and bytes_played == len(pcm_bytes)),
                cancelled=was_cancelled,
                bytes_played=bytes_played,
                duration_played_ms=dur_ms,
                device_id=self._selected_device_id,
            )

        finally:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._backend.close_stream)

    async def stop(self) -> None:
        """Stop active playback immediately and release the output device (idempotent)."""
        with self._state_lock:
            if self._state in (PlaybackState.IDLE, PlaybackState.STOPPED):
                return
            if self._state != PlaybackState.ERROR:
                self._state = PlaybackState.STOPPING
            self._cancel_event.set()

        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._backend.stop_stream)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._backend.close_stream)

        with self._state_lock:
            if self._state != PlaybackState.ERROR:
                self._state = PlaybackState.STOPPED

    async def cancel(self) -> None:
        """Cancel active playback immediately (idempotent)."""
        await self.stop()

    async def close(self) -> None:
        """Close and release all output resources (idempotent)."""
        await self.stop()

    def status_snapshot(self) -> Dict[str, object]:
        """Return safe, secret-free diagnostic status of speaker playback."""
        with self._state_lock:
            return {
                "state": self._state.value,
                "selected_device_id": self._selected_device_id,
                "backend_open": self._backend.is_open,
            }
