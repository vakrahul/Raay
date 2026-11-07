"""Voice I/O Session Lifecycle Coordinator for WinAI-OE (Milestone 2).

Coordinates `SpeechInputManager`, `SpeechOutputManager`, and optional caller-driven
`BaseSTTProvider` / `BaseTTSProvider` operations without performing any Windows
task execution or intent routing.

Hard Cancellation Guarantees:
- Stops microphone capture and speaker playback immediately.
- Cancels and awaits all `asyncio.Task` objects owned by the session (zero orphaned tasks).
- Closes all session-owned network streams/closables.
- Delivers zero audio chunks after `cancel()` or `close()`.
- Never cancels or mutates shared providers or other `VoiceIOSession` instances.
- Executes cleanup of all remaining components even if one component raises during shutdown.
"""

import asyncio
import contextlib
import secrets
import threading
import time
from typing import Any, AsyncIterator, Coroutine, Dict, List, Optional, Set

from src.voice.audio_types import (
    AudioFrame,
    AudioStateError,
    PlaybackOutcome,
    VoiceSessionState,
)
from src.voice.base import (
    BaseSTTProvider,
    BaseTTSProvider,
    STTTranscriptEvent,
    TTSAudioResult,
    TTSRequest,
    VoiceProviderError,
    sanitize_error_message,
)
from src.voice.speech_input import SpeechInputManager
from src.voice.speech_output import SpeechOutputManager


class VoiceIOSession:
    """Lifecycle coordinator for a single voice capture and playback session."""

    def __init__(
        self,
        input_manager: Optional[SpeechInputManager] = None,
        output_manager: Optional[SpeechOutputManager] = None,
        stt_provider: Optional[BaseSTTProvider] = None,
        tts_provider: Optional[BaseTTSProvider] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self.session_id = session_id or f"vsess_{secrets.token_hex(8)}"
        self.input_manager = input_manager or SpeechInputManager()
        self.output_manager = output_manager or SpeechOutputManager()
        # Shared providers are referenced but never closed/cancelled globally by this session
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider

        self._state: VoiceSessionState = VoiceSessionState.IDLE
        self._state_lock = threading.Lock()
        self._cancelled: bool = False
        self._closed: bool = False
        self._cancel_reason: Optional[str] = None
        self._terminal_error: Optional[str] = None
        self._created_at: float = time.time()

        self._owned_tasks: Set[asyncio.Task[Any]] = set()
        self._owned_closables: List[Any] = []

    @property
    def state(self) -> VoiceSessionState:
        with self._state_lock:
            return self._state

    @property
    def is_cancelled(self) -> bool:
        with self._state_lock:
            return self._cancelled

    @property
    def is_closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def active_task_count(self) -> int:
        return sum(1 for t in list(self._owned_tasks) if not t.done())

    # ------------------------------------------------------------------
    # Resource & Task Registration (Scoped Strictly to This Session)
    # ------------------------------------------------------------------

    def register_closable(self, resource: Any) -> None:
        """Register a session-owned network/stream resource to be closed on cancel/close."""
        with self._state_lock:
            if resource not in self._owned_closables:
                self._owned_closables.append(resource)

    def spawn_session_task(
        self,
        coro: Coroutine[Any, Any, Any],
        name: Optional[str] = None,
    ) -> asyncio.Task[Any]:
        """Spawn an asyncio.Task tracked by this session and automatically cancelled on shutdown."""
        with self._state_lock:
            if self._cancelled or self._closed:
                coro.close()
                raise AudioStateError(
                    code="SESSION_NOT_ACTIVE",
                    message=f"Cannot spawn task in {self._state.value} session.",
                )
            task = asyncio.create_task(coro, name=name or f"{self.session_id}_task")
            self._owned_tasks.add(task)
            task.add_done_callback(lambda t: self._owned_tasks.discard(t))
            return task

    # ------------------------------------------------------------------
    # Session Lifecycle (`start`, `start_capture`, `stop_capture`)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Explicitly activate the session (does not start microphone recording automatically)."""
        with self._state_lock:
            if self._cancelled:
                raise AudioStateError(
                    code="SESSION_CANCELLED",
                    message="Cannot restart a cancelled VoiceIOSession.",
                )
            if self._closed:
                raise AudioStateError(
                    code="SESSION_CLOSED",
                    message="Cannot restart a closed VoiceIOSession.",
                )
            if self._state == VoiceSessionState.IDLE:
                self._state = VoiceSessionState.ACTIVE

    async def start_capture(self) -> None:
        """Begin microphone capture within this session."""
        with self._state_lock:
            if self._cancelled:
                raise AudioStateError(
                    code="SESSION_CANCELLED",
                    message="Cannot start capture on a cancelled session.",
                )
            if self._closed:
                raise AudioStateError(
                    code="SESSION_CLOSED",
                    message="Cannot start capture on a closed session.",
                )
            self._state = VoiceSessionState.CAPTURING

        try:
            await self.input_manager.start()
        except Exception as e:
            with self._state_lock:
                if not self._cancelled:
                    self._state = VoiceSessionState.ERROR
                    self._terminal_error = sanitize_error_message(str(e))
            raise

        # If session was cancelled or stopped while input_manager.start() was running
        if self.is_cancelled or self.is_closed or self.state == VoiceSessionState.STOPPING:
            await self.input_manager.cancel()
            with self._state_lock:
                if self._cancelled:
                    self._state = VoiceSessionState.CANCELLED
                elif self._closed:
                    self._state = VoiceSessionState.CLOSED

    async def read_captured_frame(
        self,
        timeout_seconds: Optional[float] = 1.0,
    ) -> Optional[AudioFrame]:
        """Read next captured AudioFrame; returns None immediately if cancelled/closed."""
        if self.is_cancelled or self.is_closed:
            return None

        task = self.spawn_session_task(
            self.input_manager.read_frame(timeout_seconds=timeout_seconds),
            name=f"{self.session_id}_read_frame",
        )
        try:
            frame = await task
        except asyncio.CancelledError:
            return None
        except Exception as e:
            with self._state_lock:
                if not self._cancelled:
                    self._state = VoiceSessionState.ERROR
                    self._terminal_error = sanitize_error_message(str(e))
            raise

        if self.is_cancelled or self.is_closed:
            return None
        return frame

    async def captured_frames(
        self,
        read_timeout_seconds: float = 1.0,
    ) -> AsyncIterator[AudioFrame]:
        """Yield captured AudioFrame objects until capture stops or session is cancelled."""
        while not self.is_cancelled and not self.is_closed:
            frame = await self.read_captured_frame(timeout_seconds=read_timeout_seconds)
            if frame is None:
                break
            if self.is_cancelled or self.is_closed:
                break
            yield frame

    async def stop_capture(self) -> None:
        """Stop microphone capture while keeping the session open (idempotent)."""
        await self.input_manager.stop(clear_unread=False)
        with self._state_lock:
            if (
                self._state == VoiceSessionState.CAPTURING
                and not self._cancelled
                and not self._closed
            ):
                self._state = VoiceSessionState.ACTIVE

    # ------------------------------------------------------------------
    # Explicit Caller-Driven STT & TTS Operations
    # ------------------------------------------------------------------

    async def stream_capture_to_stt(
        self,
        language_code: str = "en-IN",
        realtime_pace: bool = False,
    ) -> AsyncIterator[STTTranscriptEvent]:
        """Explicitly stream captured microphone PCM to the configured STT provider."""
        if self.stt_provider is None:
            raise VoiceProviderError(
                code="SESSION_NO_STT_PROVIDER",
                message="No STT provider is attached to this VoiceIOSession.",
            )
        with self._state_lock:
            if self._cancelled or self._closed:
                raise AudioStateError(
                    code="SESSION_NOT_ACTIVE",
                    message=f"Cannot stream to STT while session is {self._state.value}.",
                )
            self._state = VoiceSessionState.TRANSCRIBING

        async def _guarded_pcm() -> AsyncIterator[bytes]:
            async for frame in self.captured_frames(read_timeout_seconds=1.0):
                if self.is_cancelled or self.is_closed:
                    break
                yield frame.pcm_bytes

        stream_gen = self.stt_provider.stream_pcm(
            pcm_frames=_guarded_pcm(),
            language_code=language_code,
            sample_rate=self.input_manager.format_config.sample_rate,
            realtime_pace=realtime_pace,
        )
        self.register_closable(stream_gen)

        try:
            async for event in stream_gen:
                if self.is_cancelled or self.is_closed:
                    break
                yield event
        finally:
            if hasattr(stream_gen, "aclose"):
                with contextlib.suppress(Exception):
                    await stream_gen.aclose()
            with self._state_lock:
                if (
                    self._state == VoiceSessionState.TRANSCRIBING
                    and not self._cancelled
                    and not self._closed
                ):
                    self._state = VoiceSessionState.ACTIVE

    async def play_audio(self, audio: TTSAudioResult) -> PlaybackOutcome:
        """Play validated TTSAudioResult through the session's SpeechOutputManager."""
        with self._state_lock:
            if self._cancelled or self._closed:
                raise AudioStateError(
                    code="SESSION_NOT_ACTIVE",
                    message=f"Cannot play audio while session is {self._state.value}.",
                )
            self._state = VoiceSessionState.SPEAKING

        play_task = self.spawn_session_task(
            self.output_manager.play(audio),
            name=f"{self.session_id}_play_audio",
        )
        try:
            outcome = await play_task
            with self._state_lock:
                if (
                    self._state == VoiceSessionState.SPEAKING
                    and not self._cancelled
                    and not self._closed
                ):
                    self._state = VoiceSessionState.ACTIVE
            return outcome
        except asyncio.CancelledError:
            await self.output_manager.cancel()
            return PlaybackOutcome(
                completed=False,
                cancelled=True,
                bytes_played=0,
                duration_played_ms=0.0,
                device_id=self.output_manager.selected_device_id,
            )
        except Exception as e:
            with self._state_lock:
                if not self._cancelled:
                    self._state = VoiceSessionState.ERROR
                    self._terminal_error = sanitize_error_message(str(e))
            raise

    async def synthesize_and_play(self, request: TTSRequest) -> PlaybackOutcome:
        """Synthesize speech via TTSProvider and play it on the output device."""
        if self.tts_provider is None:
            raise VoiceProviderError(
                code="SESSION_NO_TTS_PROVIDER",
                message="No TTS provider is attached to this VoiceIOSession.",
            )
        if self.is_cancelled or self.is_closed:
            raise AudioStateError(
                code="SESSION_NOT_ACTIVE",
                message=f"Cannot synthesize speech while session is {self.state.value}.",
            )
        synth_task = self.spawn_session_task(
            self.tts_provider.synthesize(request),
            name=f"{self.session_id}_tts_synthesize",
        )
        audio_res = await synth_task
        if self.is_cancelled or self.is_closed:
            return PlaybackOutcome(
                completed=False,
                cancelled=True,
                bytes_played=0,
                duration_played_ms=0.0,
            )
        return await self.play_audio(audio_res)

    # ------------------------------------------------------------------
    # Hard Cancellation & Deterministic Shutdown (`cancel`, `stop`, `close`)
    # ------------------------------------------------------------------

    async def cancel(self, reason: str = "Cancelled by caller") -> None:
        """Immediately cancel capture, playback, owned tasks, and owned streams (idempotent)."""
        with self._state_lock:
            self._cancelled = True
            if self._cancel_reason is None:
                self._cancel_reason = sanitize_error_message(reason)
            self._state = VoiceSessionState.STOPPING

        await self._teardown_all_resources(cancel_mode=True)

        with self._state_lock:
            self._state = VoiceSessionState.CANCELLED

    async def stop(self) -> None:
        """Gracefully stop capture/playback and close session-owned resources (idempotent)."""
        with self._state_lock:
            if self._cancelled:
                return
            if self._closed:
                return
            self._state = VoiceSessionState.STOPPING

        await self._teardown_all_resources(cancel_mode=False)

        with self._state_lock:
            if not self._cancelled and self._state != VoiceSessionState.ERROR:
                self._state = VoiceSessionState.CLOSED
                self._closed = True

    async def close(self) -> None:
        """Release all resources owned by this session (idempotent)."""
        with self._state_lock:
            was_cancelled = self._cancelled
            self._closed = True
            if not was_cancelled and self._state != VoiceSessionState.ERROR:
                self._state = VoiceSessionState.STOPPING

        await self._teardown_all_resources(cancel_mode=was_cancelled)

        with self._state_lock:
            if not self._cancelled and self._state != VoiceSessionState.ERROR:
                self._state = VoiceSessionState.CLOSED

    async def _teardown_all_resources(self, cancel_mode: bool) -> None:
        """Execute cleanup across input, output, owned tasks, and owned closables."""
        errors: List[Exception] = []

        # 1. Cancel all session-owned asyncio tasks first so waiting consumers wake immediately
        tasks_to_cancel = [
            t for t in list(self._owned_tasks) if not t.done() and t is not asyncio.current_task()
        ]
        for t in tasks_to_cancel:
            t.cancel()

        # 2. Stop/cancel input manager
        try:
            if cancel_mode:
                await self.input_manager.cancel()
            else:
                await self.input_manager.stop(clear_unread=True)
        except Exception as e:
            errors.append(e)

        # 3. Stop/cancel output manager
        try:
            await self.output_manager.cancel()
        except Exception as e:
            errors.append(e)

        # 4. Close session-owned network streams/closables
        with self._state_lock:
            closables = list(self._owned_closables)
            self._owned_closables.clear()

        for item in closables:
            try:
                if hasattr(item, "aclose") and callable(item.aclose):
                    await item.aclose()
                elif hasattr(item, "close") and callable(item.close):
                    res = item.close()
                    if asyncio.iscoroutine(res):
                        await res
            except Exception as e:
                errors.append(e)

        # 5. Await cancelled tasks so zero orphaned tasks remain
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        self._owned_tasks.clear()

        if errors and not cancel_mode:
            with self._state_lock:
                self._state = VoiceSessionState.ERROR
                self._terminal_error = sanitize_error_message(str(errors[0]))

    def status_snapshot(self) -> Dict[str, Any]:
        """Return sanitized session lifecycle status."""
        with self._state_lock:
            return {
                "session_id": self.session_id,
                "state": self._state.value,
                "cancelled": self._cancelled,
                "closed": self._closed,
                "cancel_reason": self._cancel_reason,
                "terminal_error": self._terminal_error,
                "active_task_count": self.active_task_count,
                "input": self.input_manager.status_snapshot(),
                "output": self.output_manager.status_snapshot(),
            }
