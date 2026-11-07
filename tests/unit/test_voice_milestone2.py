"""Deterministic offline unit tests and opt-in physical hardware tests for Milestone 2:
- SpeechInputManager (device enumeration, bounded queue, overflow policies, race safety)
- SpeechOutputManager (WAV/PCM validation, non-44-byte RIFF headers, cancellation)
- VoiceIOSession (lifecycle coordination, hard cancellation, resource isolation)
"""

import asyncio
import os
import struct
import threading
import time
from collections import deque
from typing import Deque, List, Optional

import pytest

from src.voice import (
    AudioDeviceError,
    AudioDeviceInfo,
    AudioDeviceKind,
    AudioDeviceUnavailableError,
    AudioFormatConfig,
    AudioFormatError,
    AudioQueueOverflowError,
    AudioStateError,
    BaseAudioInputBackend,
    BaseAudioOutputBackend,
    CaptureState,
    GnaniConfig,
    GnaniSTTProvider,
    GnaniTTSProvider,
    PlaybackState,
    QueueOverflowPolicy,
    SpeechInputManager,
    SpeechOutputManager,
    TTSAudioResult,
    VoiceIOSession,
    VoiceSessionState,
    build_wav_from_pcm,
)

# ---------------------------------------------------------------------------
# Deterministic Fake Audio Input & Output Backends
# ---------------------------------------------------------------------------


def make_pcm_frame(sample_val: int = 4000, num_samples: int = 512) -> bytes:
    return struct.pack(f"<{num_samples}h", *([sample_val] * num_samples))


def make_wav_with_junk_chunk(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    fmt_chunk = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    junk_chunk = b"JUNK" + struct.pack("<I", 16) + (b"\x00" * 16)
    data_chunk = struct.pack("<4sI", b"data", len(pcm_bytes)) + pcm_bytes
    wave_body = b"WAVE" + fmt_chunk + junk_chunk + data_chunk
    return struct.pack("<4sI", b"RIFF", len(wave_body)) + wave_body


class FakeAudioInputBackend(BaseAudioInputBackend):
    """Deterministic in-memory microphone backend for offline unit tests."""

    def __init__(
        self,
        devices: Optional[List[AudioDeviceInfo]] = None,
        chunks: Optional[List[Optional[bytes]]] = None,
        open_error: Optional[Exception] = None,
        read_error_after: Optional[int] = None,
        open_delay_sec: float = 0.0,
        read_delay_sec: float = 0.005,
    ) -> None:
        self._devices = (
            devices
            if devices is not None
            else [
                AudioDeviceInfo(
                    device_id=0,
                    name="Fake Default Mic",
                    kind=AudioDeviceKind.INPUT,
                    max_channels=1,
                    is_default=True,
                ),
                AudioDeviceInfo(
                    device_id=1,
                    name="Fake Headset Mic",
                    kind=AudioDeviceKind.INPUT,
                    max_channels=2,
                    is_default=False,
                ),
            ]
        )
        self._chunks: Deque[Optional[bytes]] = deque(chunks or [])
        self._open_error = open_error
        self._read_error_after = read_error_after
        self._open_delay_sec = open_delay_sec
        self._read_delay_sec = read_delay_sec

        self._open: bool = False
        self._stopped: bool = False
        self.read_calls: int = 0
        self.close_calls: int = 0
        self.opened_device_id: Optional[int] = None

    @property
    def is_open(self) -> bool:
        return self._open

    def list_devices(self) -> List[AudioDeviceInfo]:
        return list(self._devices)

    def open_stream(self, device_id: Optional[int], fmt: AudioFormatConfig) -> None:
        if self._open_delay_sec > 0:
            time.sleep(self._open_delay_sec)
        if self._open_error is not None:
            raise self._open_error
        if not self._devices:
            raise AudioDeviceUnavailableError("AUDIO_NO_INPUT_DEVICES", "No input devices.")
        self.opened_device_id = device_id
        self._open = True
        self._stopped = False

    def read_chunk(self, timeout_seconds: float = 0.1) -> Optional[bytes]:
        if not self._open or self._stopped:
            return None
        if self._read_delay_sec > 0:
            time.sleep(min(timeout_seconds, self._read_delay_sec))
        self.read_calls += 1
        if self._read_error_after is not None and self.read_calls > self._read_error_after:
            raise AudioDeviceUnavailableError(
                "AUDIO_DEVICE_DISCONNECTED", "Simulated USB microphone unplug."
            )
        if self._chunks:
            return self._chunks.popleft()
        return None

    def stop_stream(self) -> None:
        self._stopped = True

    def close_stream(self) -> None:
        self._stopped = True
        self._open = False
        self.close_calls += 1


class FakeAudioOutputBackend(BaseAudioOutputBackend):
    """Deterministic in-memory speaker backend for offline unit tests."""

    def __init__(
        self,
        devices: Optional[List[AudioDeviceInfo]] = None,
        open_error: Optional[Exception] = None,
        play_error: Optional[Exception] = None,
        chunk_delay_sec: float = 0.0,
        chunk_size: int = 1024,
    ) -> None:
        self._devices = (
            devices
            if devices is not None
            else [
                AudioDeviceInfo(
                    device_id=0,
                    name="Fake Default Speaker",
                    kind=AudioDeviceKind.OUTPUT,
                    max_channels=2,
                    is_default=True,
                ),
                AudioDeviceInfo(
                    device_id=1,
                    name="Fake USB Headphones",
                    kind=AudioDeviceKind.OUTPUT,
                    max_channels=2,
                    is_default=False,
                ),
            ]
        )
        self._open_error = open_error
        self._play_error = play_error
        self._chunk_delay_sec = chunk_delay_sec
        self._chunk_size = chunk_size

        self._open: bool = False
        self.written_pcm_chunks: List[bytes] = []
        self.close_calls: int = 0
        self.stop_calls: int = 0
        self.opened_params: Optional[tuple] = None

    @property
    def is_open(self) -> bool:
        return self._open

    def list_devices(self) -> List[AudioDeviceInfo]:
        return list(self._devices)

    def open_stream(
        self,
        device_id: Optional[int],
        sample_rate: int,
        channels: int,
        sample_width: int,
    ) -> None:
        if self._open_error is not None:
            raise self._open_error
        if not self._devices:
            raise AudioDeviceUnavailableError("AUDIO_NO_OUTPUT_DEVICES", "No output devices.")
        self.opened_params = (device_id, sample_rate, channels, sample_width)
        self._open = True

    def play_pcm(self, pcm_bytes: bytes, cancel_event: threading.Event) -> int:
        if self._play_error is not None:
            raise self._play_error
        played = 0
        for offset in range(0, len(pcm_bytes), self._chunk_size):
            if cancel_event.is_set() or not self._open:
                break
            if self._chunk_delay_sec > 0:
                time.sleep(self._chunk_delay_sec)
            if cancel_event.is_set() or not self._open:
                break
            part = pcm_bytes[offset : offset + self._chunk_size]
            self.written_pcm_chunks.append(part)
            played += len(part)
        return played

    def stop_stream(self) -> None:
        self.stop_calls += 1

    def close_stream(self) -> None:
        self._open = False
        self.close_calls += 1


# ===========================================================================
# 1. SpeechInputManager Offline Unit Tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_input_no_device_available_and_backend_init_failure():
    # 1. No device available
    empty_backend = FakeAudioInputBackend(devices=[])
    mgr = SpeechInputManager(backend=empty_backend)
    assert mgr.list_devices() == []

    with pytest.raises(AudioDeviceUnavailableError):
        await mgr.start()
    assert mgr.state == CaptureState.ERROR
    assert empty_backend.is_open is False

    # 2. Backend initialization failure wraps and cleans up
    fail_backend = FakeAudioInputBackend(open_error=RuntimeError("Driver init crash"))
    mgr2 = SpeechInputManager(backend=fail_backend)
    with pytest.raises(AudioDeviceError) as exc_info:
        await mgr2.start()
    assert exc_info.value.code == "AUDIO_INPUT_OPEN_FAILED"
    assert mgr2.state == CaptureState.ERROR
    assert fail_backend.is_open is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_input_device_enumeration_and_selection():
    backend = FakeAudioInputBackend()
    mgr = SpeechInputManager(backend=backend)
    devs = mgr.list_devices()
    assert len(devs) == 2
    assert devs[0].name == "Fake Default Mic"

    mgr.select_device(1)
    assert mgr.selected_device_id == 1

    with pytest.raises(AudioDeviceUnavailableError):
        mgr.select_device(99)

    await mgr.start()
    assert backend.opened_device_id == 1

    # Cannot switch device while recording
    with pytest.raises(AudioStateError):
        mgr.select_device(0)

    await mgr.stop()
    assert mgr.state == CaptureState.STOPPED


@pytest.mark.unit
@pytest.mark.asyncio
async def test_input_start_read_stop_repeated_stop_and_no_background_recording():
    raw_frame = make_pcm_frame(sample_val=8192, num_samples=512)
    backend = FakeAudioInputBackend(chunks=[raw_frame, raw_frame])
    mgr = SpeechInputManager(backend=backend)

    # Privacy guarantee: zero background reads before explicit start()
    assert mgr.state == CaptureState.IDLE
    assert backend.read_calls == 0

    await mgr.start()
    assert mgr.state == CaptureState.RECORDING

    f1 = await mgr.read_frame(timeout_seconds=1.0)
    f2 = await mgr.read_frame(timeout_seconds=1.0)
    assert f1 is not None and f1.sequence == 1
    assert f2 is not None and f2.sequence == 2
    assert f1.size_bytes == 1024
    assert f1.rms > 0.2

    await mgr.stop()
    assert mgr.state == CaptureState.STOPPED
    assert backend.is_open is False

    # Repeated stop() calls must be safe no-ops
    await mgr.stop()
    await mgr.stop()
    assert mgr.state == CaptureState.STOPPED

    # No audio delivered after stop
    assert await mgr.read_frame(timeout_seconds=0.1) is None

    # No background reads after stop
    calls_after_stop = backend.read_calls
    await asyncio.sleep(0.05)
    assert backend.read_calls == calls_after_stop


@pytest.mark.unit
@pytest.mark.asyncio
async def test_input_cancellation_and_empty_frames_skipped():
    raw_frame = make_pcm_frame(sample_val=4000, num_samples=512)
    # Include an empty b"" chunk before a valid chunk
    backend = FakeAudioInputBackend(
        chunks=[b"", raw_frame, raw_frame, raw_frame],
        read_delay_sec=0.002,
    )
    mgr = SpeechInputManager(backend=backend)
    await mgr.start()

    first = await mgr.read_frame(timeout_seconds=1.0)
    assert first is not None
    assert first.size_bytes == 1024
    assert mgr.status_snapshot()["empty_chunks_skipped"] >= 1

    # Cancel while unread frames remain in queue -> must purge queue immediately
    await mgr.cancel()
    assert mgr.state == CaptureState.STOPPED
    assert backend.is_open is False
    assert await mgr.read_frame(timeout_seconds=0.1) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_input_device_disconnection_cleanup():
    raw_frame = make_pcm_frame()
    backend = FakeAudioInputBackend(chunks=[raw_frame], read_error_after=1)
    mgr = SpeechInputManager(backend=backend)
    await mgr.start()

    f1 = await mgr.read_frame(timeout_seconds=1.0)
    assert f1 is not None

    with pytest.raises(AudioDeviceUnavailableError) as exc_disc:
        await mgr.read_frame(timeout_seconds=1.0)
    assert exc_disc.value.code == "AUDIO_DEVICE_DISCONNECTED"
    assert mgr.state == CaptureState.ERROR
    assert backend.is_open is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_input_bounded_queue_overflow_policies_when_consumer_slower():
    frames_10 = [make_pcm_frame(sample_val=(i + 1) * 1000) for i in range(10)]

    # 1. DROP_OLDEST (capacity=4): keeps newest 4 frames (sequences 7, 8, 9, 10)
    cfg_oldest = AudioFormatConfig(
        max_queue_frames=4,
        overflow_policy=QueueOverflowPolicy.DROP_OLDEST,
    )
    backend_oldest = FakeAudioInputBackend(chunks=list(frames_10), read_delay_sec=0.001)
    mgr_oldest = SpeechInputManager(format_config=cfg_oldest, backend=backend_oldest)
    await mgr_oldest.start()
    await asyncio.sleep(0.06)  # Let producer enqueue all 10 frames while consumer waits
    await mgr_oldest.stop(clear_unread=False)

    assert mgr_oldest.dropped_frames_count == 6
    read_seqs = []
    while len(read_seqs) < 4:
        fr = await mgr_oldest.read_frame(timeout_seconds=0.1)
        if fr is None:
            break
        read_seqs.append(fr.sequence)
    assert read_seqs == [7, 8, 9, 10]

    # 2. DROP_NEWEST (capacity=4): keeps first 4 frames (sequences 1, 2, 3, 4)
    cfg_newest = AudioFormatConfig(
        max_queue_frames=4,
        overflow_policy=QueueOverflowPolicy.DROP_NEWEST,
    )
    backend_newest = FakeAudioInputBackend(chunks=list(frames_10), read_delay_sec=0.001)
    mgr_newest = SpeechInputManager(format_config=cfg_newest, backend=backend_newest)
    await mgr_newest.start()
    await asyncio.sleep(0.06)
    await mgr_newest.stop(clear_unread=False)

    assert mgr_newest.dropped_frames_count == 6
    read_seqs_new = []
    while len(read_seqs_new) < 4:
        fr = await mgr_newest.read_frame(timeout_seconds=0.1)
        if fr is None:
            break
        read_seqs_new.append(fr.sequence)
    assert read_seqs_new == [1, 2, 3, 4]

    # 3. RAISE_ERROR (capacity=4): raises AudioQueueOverflowError
    cfg_err = AudioFormatConfig(
        max_queue_frames=4,
        overflow_policy=QueueOverflowPolicy.RAISE_ERROR,
    )
    backend_err = FakeAudioInputBackend(chunks=list(frames_10), read_delay_sec=0.001)
    mgr_err = SpeechInputManager(format_config=cfg_err, backend=backend_err)
    await mgr_err.start()
    await asyncio.sleep(0.06)

    with pytest.raises(AudioQueueOverflowError):
        for _ in range(6):
            await mgr_err.read_frame(timeout_seconds=0.2)
    assert mgr_err.state == CaptureState.ERROR
    assert backend_err.is_open is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_input_concurrent_start_stop_cancel_operations():
    backend = FakeAudioInputBackend(
        chunks=[make_pcm_frame() for _ in range(20)],
        open_delay_sec=0.03,
    )
    mgr = SpeechInputManager(backend=backend)

    # Issue cancel() while start() is still inside open_stream
    async def delayed_cancel():
        await asyncio.sleep(0.01)
        await mgr.cancel()
        await mgr.stop()

    await asyncio.gather(mgr.start(), delayed_cancel())
    assert mgr.state == CaptureState.STOPPED
    assert backend.is_open is False
    assert await mgr.read_frame(timeout_seconds=0.05) is None


# ===========================================================================
# 2. SpeechOutputManager Offline Unit Tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_output_valid_wav_and_raw_pcm_playback():
    raw_pcm = make_pcm_frame(sample_val=6000, num_samples=1600)  # 3200 bytes = 100ms @ 16kHz
    wav_with_junk = make_wav_with_junk_chunk(raw_pcm, sample_rate=16000)

    backend = FakeAudioOutputBackend()
    out = SpeechOutputManager(backend=backend)

    # 1. Play TTSAudioResult (WAV with JUNK subchunk shifting data past 44 bytes)
    tts_wav = TTSAudioResult(
        audio_bytes=wav_with_junk,
        container="wav",
        mime_type="audio/wav",
        encoding="linear_pcm",
        sample_rate=16000,
        num_channels=1,
        sample_width_bytes=2,
        duration_seconds=0.1,
        model="timbre-v3.0",
        voice="Shivani",
        language="en-IN",
    )
    res_wav = await out.play(tts_wav)
    assert res_wav.completed is True
    assert res_wav.cancelled is False
    # Verify only the 3200 PCM payload bytes were written (header stripped!)
    assert res_wav.bytes_played == len(raw_pcm)
    assert b"".join(backend.written_pcm_chunks) == raw_pcm
    assert backend.is_open is False
    assert out.state == PlaybackState.STOPPED

    # 2. Play raw PCM with explicit metadata
    backend.written_pcm_chunks.clear()
    res_pcm = await out.play(
        raw_pcm,
        container="raw",
        sample_rate=16000,
        channels=1,
        sample_width=2,
    )
    assert res_pcm.completed is True
    assert res_pcm.bytes_played == len(raw_pcm)
    assert backend.is_open is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_output_format_validation_and_unsupported_codecs():
    backend = FakeAudioOutputBackend()
    out = SpeechOutputManager(backend=backend)

    # 1. Arbitrary bytes without container rejected
    with pytest.raises(AudioFormatError) as exc_no_cont:
        await out.play(b"\x00\x01\x02\x03")
    assert exc_no_cont.value.code == "AUDIO_MISSING_CONTAINER"

    # 2. Invalid WAV data rejected
    with pytest.raises(AudioFormatError):
        await out.play(b"NOT_A_RIFF_WAV_HEADER" * 5, container="wav")

    # 3. Unsupported compressed format (mp3 / ogg) rejected clearly
    mp3_res = TTSAudioResult(
        audio_bytes=b"\xff\xfb\x90\x64" * 100,
        container="mp3",
        mime_type="audio/mpeg",
        encoding="linear_pcm",
        sample_rate=24000,
        num_channels=1,
        sample_width_bytes=2,
        model="timbre-v3.0",
        voice="Shivani",
        language="en-IN",
    )
    with pytest.raises(AudioFormatError) as exc_mp3:
        await out.play(mp3_res)
    assert exc_mp3.value.code == "AUDIO_UNSUPPORTED_CONTAINER"

    # 4. Raw PCM missing sample_rate/channels/sample_width rejected
    with pytest.raises(AudioFormatError) as exc_meta:
        await out.play(b"\x00\x00" * 100, container="raw")
    assert exc_meta.value.code == "AUDIO_MISSING_PCM_METADATA"
    assert backend.close_calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_output_device_enumeration_missing_device_and_backend_failures():
    # 1. Missing output device
    no_dev_out = SpeechOutputManager(backend=FakeAudioOutputBackend(devices=[]))
    assert no_dev_out.list_devices() == []
    wav_bytes = build_wav_from_pcm(make_pcm_frame(), sample_rate=16000)

    with pytest.raises(AudioDeviceUnavailableError):
        await no_dev_out.play(wav_bytes, container="wav")
    assert no_dev_out.state == PlaybackState.ERROR

    # 2. Playback failure mid-stream releases device
    fail_backend = FakeAudioOutputBackend(play_error=RuntimeError("Speaker unplugged"))
    out_fail = SpeechOutputManager(backend=fail_backend)
    with pytest.raises(AudioDeviceError) as exc_play:
        await out_fail.play(wav_bytes, container="wav")
    assert exc_play.value.code == "AUDIO_OUTPUT_PLAY_FAILED"
    assert out_fail.state == PlaybackState.ERROR
    assert fail_backend.is_open is False
    assert fail_backend.close_calls >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_output_stop_cancel_non_overlapping_and_repeated_stop():
    # Create 8 chunks of 1024 bytes with 25ms delay per chunk (~200ms total)
    long_pcm = make_pcm_frame(num_samples=512) * 8
    wav_bytes = build_wav_from_pcm(long_pcm, sample_rate=16000)

    backend = FakeAudioOutputBackend(chunk_delay_sec=0.025, chunk_size=1024)
    out = SpeechOutputManager(backend=backend)

    play_task = asyncio.create_task(out.play(wav_bytes, container="wav"))
    await asyncio.sleep(0.03)
    assert out.state == PlaybackState.PLAYING

    # Overlapping playback must be rejected with AudioStateError
    with pytest.raises(AudioStateError) as exc_overlap:
        await out.play(wav_bytes, container="wav")
    assert exc_overlap.value.code == "AUDIO_PLAYBACK_OVERLAPPING"

    # Cancel active playback mid-stream
    await out.cancel()
    outcome = await play_task
    assert outcome.completed is False
    assert outcome.cancelled is True
    assert outcome.bytes_played < len(long_pcm)
    assert backend.is_open is False
    assert out.state == PlaybackState.STOPPED

    # Repeated stop/cancel calls are safe
    await out.stop()
    await out.cancel()
    await out.close()
    assert out.state == PlaybackState.STOPPED


# ===========================================================================
# 3. VoiceIOSession Lifecycle & Cancellation Unit Tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_startup_capture_playback_and_clean_shutdown():
    in_backend = FakeAudioInputBackend(chunks=[make_pcm_frame(), make_pcm_frame()])
    out_backend = FakeAudioOutputBackend()
    sess = VoiceIOSession(
        input_manager=SpeechInputManager(backend=in_backend),
        output_manager=SpeechOutputManager(backend=out_backend),
    )

    assert sess.state == VoiceSessionState.IDLE
    await sess.start()
    assert sess.state == VoiceSessionState.ACTIVE
    assert in_backend.is_open is False

    await sess.start_capture()
    assert sess.state == VoiceSessionState.CAPTURING

    frame = await sess.read_captured_frame(timeout_seconds=1.0)
    assert frame is not None and frame.size_bytes == 1024

    await sess.stop_capture()
    assert sess.state == VoiceSessionState.ACTIVE
    assert in_backend.is_open is False

    wav_res = TTSAudioResult(
        audio_bytes=build_wav_from_pcm(make_pcm_frame(), sample_rate=16000),
        container="wav",
        mime_type="audio/wav",
        encoding="linear_pcm",
        sample_rate=16000,
        num_channels=1,
        sample_width_bytes=2,
        model="timbre-v3.0",
        voice="Shivani",
        language="en-IN",
    )
    outcome = await sess.play_audio(wav_res)
    assert outcome.completed is True

    await sess.close()
    assert sess.state == VoiceSessionState.CLOSED
    assert sess.is_closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_cancel_while_consumer_waiting_and_no_orphaned_tasks():
    # Backend with no chunks so read_captured_frame blocks waiting for audio
    in_backend = FakeAudioInputBackend(chunks=[], read_delay_sec=0.05)
    sess = VoiceIOSession(
        input_manager=SpeechInputManager(backend=in_backend),
        output_manager=SpeechOutputManager(backend=FakeAudioOutputBackend()),
    )
    await sess.start()
    await sess.start_capture()

    # Spawn consumer waiting for audio and a custom background task in the session
    consumer_task = asyncio.create_task(sess.read_captured_frame(timeout_seconds=10.0))
    bg_task = sess.spawn_session_task(asyncio.sleep(10.0), name="long_bg_task")
    await asyncio.sleep(0.03)

    # Cancel session -> must wake consumer immediately and cancel bg_task
    await sess.cancel(reason="User pressed Escape")
    result = await consumer_task
    assert result is None
    assert bg_task.cancelled() or bg_task.done()
    assert sess.active_task_count == 0
    assert sess.state == VoiceSessionState.CANCELLED
    assert in_backend.is_open is False

    # Repeated cancel calls are safe
    await sess.cancel()
    await sess.cancel()
    assert sess.state == VoiceSessionState.CANCELLED

    # Cancelled session cannot restart or deliver audio
    assert await sess.read_captured_frame(timeout_seconds=0.1) is None
    with pytest.raises(AudioStateError):
        await sess.start_capture()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_cancel_while_startup_in_progress():
    in_backend = FakeAudioInputBackend(chunks=[make_pcm_frame()], open_delay_sec=0.04)
    sess = VoiceIOSession(
        input_manager=SpeechInputManager(backend=in_backend),
        output_manager=SpeechOutputManager(backend=FakeAudioOutputBackend()),
    )
    await sess.start()

    async def cancel_during_open():
        await asyncio.sleep(0.01)
        await sess.cancel()

    await asyncio.gather(sess.start_capture(), cancel_during_open())
    assert sess.state == VoiceSessionState.CANCELLED
    assert in_backend.is_open is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_isolation_and_resilient_cleanup_on_component_failure():
    """Verify cancelling Session A closes Session A's resources without affecting Session B."""
    shared_cfg = GnaniConfig.from_env_or_vault(
        api_key="vach_shared_dummy_key_1234567890", allow_vault_lookup=False
    )
    shared_stt = GnaniSTTProvider(config=shared_cfg)
    shared_tts = GnaniTTSProvider(config=shared_cfg)

    closed_flag = {"network_stream_closed": False}

    class FailingClosable:
        async def aclose(self):
            raise RuntimeError("Simulated socket close error")

    class TrackedClosable:
        async def aclose(self):
            closed_flag["network_stream_closed"] = True

    sess_a = VoiceIOSession(
        input_manager=SpeechInputManager(backend=FakeAudioInputBackend(chunks=[make_pcm_frame()])),
        output_manager=SpeechOutputManager(backend=FakeAudioOutputBackend()),
        stt_provider=shared_stt,
        tts_provider=shared_tts,
    )
    sess_b = VoiceIOSession(
        input_manager=SpeechInputManager(backend=FakeAudioInputBackend(chunks=[make_pcm_frame()])),
        output_manager=SpeechOutputManager(backend=FakeAudioOutputBackend()),
        stt_provider=shared_stt,
        tts_provider=shared_tts,
    )

    await sess_a.start()
    await sess_a.start_capture()
    sess_a.register_closable(FailingClosable())
    sess_a.register_closable(TrackedClosable())

    await sess_b.start()
    await sess_b.start_capture()

    # Cancel Session A -> FailingClosable throws, but TrackedClosable and devices must still close!
    await sess_a.cancel()
    assert closed_flag["network_stream_closed"] is True
    assert sess_a.state == VoiceSessionState.CANCELLED

    # Session B and shared providers must remain completely unaffected
    assert sess_b.state == VoiceSessionState.CAPTURING
    assert shared_stt.circuit_breaker.allow_request() is True
    assert shared_tts.circuit_breaker.allow_request() is True
    frame_b = await sess_b.read_captured_frame(timeout_seconds=1.0)
    assert frame_b is not None
    await sess_b.close()


# ===========================================================================
# 4. Opt-In Physical Audio Hardware Tests (`WINAI_RUN_AUDIO_DEVICE_TESTS=1`)
# ===========================================================================


@pytest.mark.skipif(
    os.environ.get("WINAI_RUN_AUDIO_DEVICE_TESTS") != "1",
    reason="Physical audio hardware tests require explicit WINAI_RUN_AUDIO_DEVICE_TESTS=1 opt-in.",
)
@pytest.mark.asyncio
async def test_opt_in_physical_audio_device_enumeration_and_bounded_capture():
    mgr_in = SpeechInputManager()
    mgr_out = SpeechOutputManager()

    in_devs = mgr_in.list_devices()
    out_devs = mgr_out.list_devices()
    if not in_devs:
        pytest.skip("No physical microphone input devices reported by WinMM.")

    explicit_id = os.environ.get("WINAI_TEST_INPUT_DEVICE_ID")
    if len(in_devs) > 1 and explicit_id is None:
        pytest.skip(
            "Multiple input devices detected; set WINAI_TEST_INPUT_DEVICE_ID to select a device."
        )
    if explicit_id is not None:
        mgr_in.select_device(int(explicit_id))

    await mgr_in.start()
    try:
        frame = await mgr_in.read_frame(timeout_seconds=1.5)
        assert frame is not None
        assert frame.size_bytes == mgr_in.format_config.frame_bytes
    finally:
        await mgr_in.close()

    assert mgr_in.state == CaptureState.STOPPED
    assert isinstance(out_devs, list)
