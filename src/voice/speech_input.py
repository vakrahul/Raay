"""Explicit, controllable microphone capture manager for WinAI-OE (Milestone 2).

Guarantees:
- Microphone remains completely inactive (`IDLE`) until `start()` is explicitly called.
- Non-blocking asyncio integration via a bounded thread-safe ring buffer.
- Explicit overflow policies (`DROP_OLDEST`, `DROP_NEWEST`, `RAISE_ERROR`) when consumer lags.
- Deterministic resource release on `stop()`, `cancel()`, `close()`, or device errors.
- Zero background recording after shutdown; zero implicit disk persistence or network upload.
"""

import asyncio
import contextlib
import threading
import time
from collections import deque
from typing import AsyncIterator, Deque, Dict, List, Optional

from src.voice.audio_devices import BaseAudioInputBackend, WinMMAudioInputBackend
from src.voice.audio_types import (
    AudioDeviceError,
    AudioDeviceInfo,
    AudioDeviceUnavailableError,
    AudioFormatConfig,
    AudioFrame,
    AudioQueueOverflowError,
    AudioStateError,
    CaptureState,
    QueueOverflowPolicy,
    compute_pcm_rms,
)


class SpeechInputManager:
    """Manages microphone capture lifecycle, bounded buffering, and async frame delivery."""

    def __init__(
        self,
        format_config: Optional[AudioFormatConfig] = None,
        backend: Optional[BaseAudioInputBackend] = None,
        device_id: Optional[int] = None,
    ) -> None:
        self.format_config = format_config or AudioFormatConfig()
        self._backend: BaseAudioInputBackend = backend or WinMMAudioInputBackend()
        self._selected_device_id: Optional[int] = device_id

        self._state: CaptureState = CaptureState.IDLE
        self._state_lock = threading.Lock()
        self._queue: Deque[AudioFrame] = deque()
        self._stop_event = threading.Event()
        self._cancel_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._frame_ready_event: Optional[asyncio.Event] = None

        self._sequence: int = 0
        self._bytes_captured: int = 0
        self._dropped_frames_count: int = 0
        self._empty_chunks_skipped: int = 0
        self._worker_error: Optional[AudioDeviceError] = None

    @property
    def state(self) -> CaptureState:
        with self._state_lock:
            return self._state

    @property
    def selected_device_id(self) -> Optional[int]:
        return self._selected_device_id

    @property
    def dropped_frames_count(self) -> int:
        with self._state_lock:
            return self._dropped_frames_count

    @property
    def bytes_captured(self) -> int:
        with self._state_lock:
            return self._bytes_captured

    @property
    def is_recording(self) -> bool:
        return self.state == CaptureState.RECORDING

    # ------------------------------------------------------------------
    # Device Enumeration & Selection
    # ------------------------------------------------------------------

    def list_devices(self) -> List[AudioDeviceInfo]:
        """Enumerate available microphone input devices."""
        return self._backend.list_devices()

    def select_device(self, device_id: Optional[int]) -> None:
        """Select an input device ID (or None for default mapper) before starting capture."""
        if self.state in (CaptureState.STARTING, CaptureState.RECORDING):
            raise AudioStateError(
                code="AUDIO_INPUT_BUSY",
                message="Cannot change input device while capture is active.",
            )
        if device_id is not None:
            devices = self._backend.list_devices()
            valid_ids = {d.device_id for d in devices}
            if valid_ids and device_id not in valid_ids:
                ids_list = sorted(valid_ids)
                raise AudioDeviceUnavailableError(
                    code="AUDIO_INPUT_DEVICE_NOT_FOUND",
                    message=f"Input device_id {device_id} not found in devices ({ids_list}).",
                )
        self._selected_device_id = device_id

    # ------------------------------------------------------------------
    # Capture Lifecycle (`start`, `stop`, `cancel`, `close`)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Explicitly start a microphone capture session."""
        with self._state_lock:
            if self._state in (CaptureState.STARTING, CaptureState.RECORDING):
                raise AudioStateError(
                    code="AUDIO_CAPTURE_ALREADY_ACTIVE",
                    message=f"Cannot start capture while in state {self._state.value}.",
                )
            self._state = CaptureState.STARTING
            self._queue.clear()
            self._stop_event.clear()
            self._cancel_event.clear()
            self._sequence = 0
            self._bytes_captured = 0
            self._dropped_frames_count = 0
            self._empty_chunks_skipped = 0
            self._worker_error = None

        self._loop = asyncio.get_running_loop()
        self._frame_ready_event = asyncio.Event()

        try:
            await asyncio.to_thread(
                self._backend.open_stream,
                self._selected_device_id,
                self.format_config,
            )
        except AudioDeviceError as e:
            with self._state_lock:
                self._state = CaptureState.ERROR
                self._worker_error = e
            await asyncio.to_thread(self._backend.close_stream)
            raise
        except Exception as e:
            wrapped = AudioDeviceError(
                code="AUDIO_INPUT_OPEN_FAILED",
                message=f"Failed to initialize microphone capture: {e}",
            )
            with self._state_lock:
                self._state = CaptureState.ERROR
                self._worker_error = wrapped
            await asyncio.to_thread(self._backend.close_stream)
            raise wrapped from e

        # Check if stop() or cancel() was requested concurrently during STARTING
        if self._stop_event.is_set() or self._cancel_event.is_set():
            await asyncio.to_thread(self._backend.close_stream)
            with self._state_lock:
                self._queue.clear()
                self._state = CaptureState.STOPPED
            self._notify_waiters()
            return

        with self._state_lock:
            self._state = CaptureState.RECORDING
            self._worker_thread = threading.Thread(
                target=self._capture_worker_loop,
                name="WinAI-SpeechInputWorker",
                daemon=True,
            )
            self._worker_thread.start()

    def _notify_waiters(self) -> None:
        loop = self._loop
        ev = self._frame_ready_event
        if loop is not None and ev is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(ev.set)

    def _capture_worker_loop(self) -> None:
        """Background thread reading frames from the backend into the bounded queue."""
        try:
            while not self._stop_event.is_set() and not self._cancel_event.is_set():
                chunk = self._backend.read_chunk(timeout_seconds=0.05)
                if self._stop_event.is_set() or self._cancel_event.is_set():
                    break
                if chunk is None:
                    continue
                if len(chunk) == 0:
                    with self._state_lock:
                        self._empty_chunks_skipped += 1
                    continue

                rms = compute_pcm_rms(chunk, sample_width=self.format_config.sample_width)
                bps = self.format_config.bytes_per_second
                dur_ms = round((len(chunk) / float(bps)) * 1000.0, 2) if bps > 0 else 0.0

                with self._state_lock:
                    if self._stop_event.is_set() or self._cancel_event.is_set():
                        break

                    if self._bytes_captured + len(chunk) > self.format_config.max_session_bytes:
                        max_s = self.format_config.max_session_bytes
                        self._worker_error = AudioQueueOverflowError(
                            code="AUDIO_SESSION_MAX_BYTES",
                            message=f"Capture session exceeded max_session_bytes ({max_s}).",
                        )
                        self._state = CaptureState.ERROR
                        self._stop_event.set()
                        break

                    if len(self._queue) >= self.format_config.max_queue_frames:
                        policy = self.format_config.overflow_policy
                        if policy == QueueOverflowPolicy.DROP_OLDEST:
                            self._queue.popleft()
                            self._dropped_frames_count += 1
                        elif policy == QueueOverflowPolicy.DROP_NEWEST:
                            self._dropped_frames_count += 1
                            continue
                        else:
                            max_q = self.format_config.max_queue_frames
                            self._worker_error = AudioQueueOverflowError(
                                code="AUDIO_QUEUE_OVERFLOW",
                                message=f"Audio capture queue reached capacity ({max_q} frames).",
                            )
                            self._state = CaptureState.ERROR
                            self._stop_event.set()
                            break

                    self._sequence += 1
                    self._bytes_captured += len(chunk)
                    frame = AudioFrame(
                        pcm_bytes=chunk,
                        sequence=self._sequence,
                        sample_rate=self.format_config.sample_rate,
                        channels=self.format_config.channels,
                        sample_width=self.format_config.sample_width,
                        rms=rms,
                        duration_ms=dur_ms,
                    )
                    self._queue.append(frame)

                self._notify_waiters()

        except AudioDeviceError as e:
            with self._state_lock:
                self._worker_error = e
                self._state = CaptureState.ERROR
                self._stop_event.set()
        except Exception as e:
            with self._state_lock:
                self._worker_error = AudioDeviceUnavailableError(
                    code="AUDIO_DEVICE_DISCONNECTED",
                    message=f"Microphone stream interrupted or disconnected: {e}",
                )
                self._state = CaptureState.ERROR
                self._stop_event.set()
        finally:
            with contextlib.suppress(Exception):
                self._backend.stop_stream()
            with contextlib.suppress(Exception):
                self._backend.close_stream()
            self._notify_waiters()

    async def stop(self, clear_unread: bool = True) -> None:
        """Stop microphone capture and release the hardware device (idempotent)."""
        with self._state_lock:
            if self._state in (CaptureState.IDLE, CaptureState.STOPPED):
                if clear_unread:
                    self._queue.clear()
                return
            if self._state != CaptureState.ERROR:
                self._state = CaptureState.STOPPING
            self._stop_event.set()
            worker = self._worker_thread

        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._backend.stop_stream)

        if worker is not None and worker.is_alive():
            await asyncio.to_thread(worker.join, 2.0)

        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._backend.close_stream)

        with self._state_lock:
            self._worker_thread = None
            if clear_unread:
                self._queue.clear()
            if self._state != CaptureState.ERROR:
                self._state = CaptureState.STOPPED

        self._notify_waiters()

    async def cancel(self) -> None:
        """Immediately abort capture, purge buffered frames, and release hardware (idempotent)."""
        self._cancel_event.set()
        self._stop_event.set()
        with self._state_lock:
            self._queue.clear()
        await self.stop(clear_unread=True)

    async def close(self) -> None:
        """Close and release all capture resources."""
        await self.cancel()

    # ------------------------------------------------------------------
    # Reading Captured Frames (`read_frame`, `frames`, `pcm_chunks`)
    # ------------------------------------------------------------------

    async def read_frame(self, timeout_seconds: Optional[float] = 2.0) -> Optional[AudioFrame]:
        """Read the next captured AudioFrame, or return None when capture has stopped/cancelled."""
        deadline = (time.monotonic() + timeout_seconds) if timeout_seconds is not None else None

        while True:
            with self._state_lock:
                if self._cancel_event.is_set():
                    self._queue.clear()
                    return None

                if self._queue:
                    return self._queue.popleft()

                if self._worker_error is not None:
                    err = self._worker_error
                    self._worker_error = None
                    raise err

                if self._state in (CaptureState.IDLE, CaptureState.STOPPED, CaptureState.ERROR):
                    return None

                if self._stop_event.is_set() and not (
                    self._worker_thread and self._worker_thread.is_alive()
                ):
                    return None

            ev = self._frame_ready_event
            if ev is None:
                return None

            ev.clear()
            # Re-check queue after clearing event to avoid lost wakeups
            with self._state_lock:
                if (
                    self._queue
                    or self._worker_error is not None
                    or self._stop_event.is_set()
                    or self._cancel_event.is_set()
                ):
                    continue

            wait_time = 0.1
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                wait_time = min(0.25, remaining)

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ev.wait(), timeout=wait_time)

    async def frames(self, read_timeout_seconds: float = 1.0) -> AsyncIterator[AudioFrame]:
        """Async generator yielding captured AudioFrame objects until capture stops or cancels."""
        while True:
            frame = await self.read_frame(timeout_seconds=read_timeout_seconds)
            if frame is None:
                break
            yield frame

    async def pcm_chunks(self, read_timeout_seconds: float = 1.0) -> AsyncIterator[bytes]:
        """Async generator yielding raw PCM byte chunks for GnaniSTTProvider.stream_pcm."""
        async for frame in self.frames(read_timeout_seconds=read_timeout_seconds):
            yield frame.pcm_bytes

    def status_snapshot(self) -> Dict[str, object]:
        """Return safe, secret-free diagnostic status of microphone capture."""
        with self._state_lock:
            return {
                "state": self._state.value,
                "selected_device_id": self._selected_device_id,
                "sample_rate": self.format_config.sample_rate,
                "channels": self.format_config.channels,
                "frame_bytes": self.format_config.frame_bytes,
                "queued_frames": len(self._queue),
                "sequence": self._sequence,
                "bytes_captured": self._bytes_captured,
                "dropped_frames_count": self._dropped_frames_count,
                "empty_chunks_skipped": self._empty_chunks_skipped,
                "backend_open": self._backend.is_open,
            }
