"""Deterministic offline unit tests and opt-in live tests for Milestone 1:
- GnaniSTTProvider (Prisma v2.5 REST & WebSocket)
- GnaniTTSProvider (Timbre v2.5 & v3.0 REST & SSE, RIFF/WAV sentinel parser)
- CapabilityRegistry (Independent, truthful STT & TTS capability states)
- Credential security and redaction across exceptions, headers, and serialized reports.
"""

import asyncio
import base64
import io
import json
import os
import struct
import time
import wave
from typing import Any, AsyncIterator, List, Optional

import httpx
import pytest
import websockets

from src.voice import (
    WAV_SENTINEL_SIZE,
    CapabilityRegistry,
    CapabilityStatus,
    GnaniConfig,
    GnaniSTTProvider,
    GnaniTTSProvider,
    SegmentStitcher,
    STTTranscriptEvent,
    TranscriptEventKind,
    TTSAudioConfig,
    TTSRequest,
    VADProfile,
    VADProfileController,
    VoiceAuthError,
    VoiceConfigError,
    VoiceConnectionError,
    VoiceInvalidAudioError,
    VoiceRateLimitError,
    VoiceTimeoutError,
    build_wav_from_pcm,
    parse_wav_container,
    resolve_stt_output_format,
    sanitize_bias_list,
)

SYNTHETIC_API_KEY = "vach_synthetic_unit_test_credential_0123456789abcdef"


# ---------------------------------------------------------------------------
# Synthetic Audio & Fake Transport Helpers
# ---------------------------------------------------------------------------


def make_synthetic_pcm(duration_ms: int = 200, sample_rate: int = 16000) -> bytes:
    """Create deterministic non-zero 16-bit mono PCM audio bytes."""
    num_samples = int(sample_rate * (duration_ms / 1000.0))
    samples = [((i * 137) % 16000) - 8000 for i in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


def make_synthetic_wav(
    duration_ms: int = 200,
    sample_rate: int = 16000,
    sentinel_sizes: bool = False,
    extra_junk_chunk: bool = False,
) -> bytes:
    """Create a synthetic RIFF WAVE container, optionally with 0xFFFFFFFF sentinel sizes."""
    pcm = make_synthetic_pcm(duration_ms=duration_ms, sample_rate=sample_rate)
    if not extra_junk_chunk and not sentinel_sizes:
        return build_wav_from_pcm(pcm, sample_rate=sample_rate, num_channels=1, sample_width=2)

    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
    )
    junk_chunk = b"JUNK" + struct.pack("<I", 12) + (b"\x00" * 12) if extra_junk_chunk else b""
    data_size_field = WAV_SENTINEL_SIZE if sentinel_sizes else len(pcm)
    data_header = struct.pack("<4sI", b"data", data_size_field)
    body = b"WAVE" + fmt_chunk + junk_chunk + data_header + pcm
    riff_size_field = WAV_SENTINEL_SIZE if sentinel_sizes else len(body)
    return struct.pack("<4sI", b"RIFF", riff_size_field) + body


async def async_pcm_chunks(data: bytes, chunk_size: int = 512) -> AsyncIterator[bytes]:
    for idx in range(0, len(data), chunk_size):
        yield data[idx : idx + chunk_size]


class FakeWebSocketSession:
    """Deterministic fake WebSocket session tracking cleanup and sent frames."""

    def __init__(
        self,
        incoming_frames: List[Any],
        raise_on_recv: Optional[Exception] = None,
        delay_recv_seconds: float = 0.0,
    ):
        self._incoming = list(incoming_frames)
        self._raise_on_recv = raise_on_recv
        self._delay_recv = delay_recv_seconds
        self.sent_frames: List[Any] = []
        self.closed: bool = False

    async def send(self, data: Any) -> None:
        if self.closed:
            raise websockets.ConnectionClosed(None, None)
        self.sent_frames.append(data)

    async def recv(self) -> Any:
        if self._delay_recv > 0:
            await asyncio.sleep(self._delay_recv)
        if self._incoming:
            item = self._incoming.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self._raise_on_recv is not None:
            raise self._raise_on_recv
        raise websockets.ConnectionClosed(None, None)

    async def close(self) -> None:
        self.closed = True


def make_test_config(api_key: Optional[str] = SYNTHETIC_API_KEY, **kwargs: Any) -> GnaniConfig:
    return GnaniConfig.from_env_or_vault(
        api_key=api_key,
        allow_vault_lookup=False,
        **kwargs,
    )


# ===========================================================================
# 1. GnaniSTTProvider Unit Tests (Prisma v2.5)
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_missing_api_key(monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    monkeypatch.delenv("VACHANA_API_KEY", raising=False)
    cfg = make_test_config(api_key="")
    stt = GnaniSTTProvider(config=cfg)
    assert stt.is_configured is False

    with pytest.raises(VoiceConfigError) as exc_info:
        await stt.transcribe_bytes(make_synthetic_wav())
    assert exc_info.value.code == "VOICE_KEY_MISSING"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_ws_documented_five_message_types_only_and_no_x_partials_header():
    """Verify only the 5 documented server message types are handled and x-partials is not sent."""
    captured_headers = {}
    ws_session = FakeWebSocketSession(
        [
            json.dumps({"type": "connected", "config": {"sample_rate": 16000, "chunk_size": 512}}),
            json.dumps({"type": "speech_start", "utterance_id": "u_ignored"}),
            json.dumps(
                {
                    "type": "partial_transcript",
                    "text": "MUST_BE_IGNORED_NOT_IN_DOCS",
                }
            ),
            json.dumps({"type": "processing", "timestamp": "2026-10-23T10:00:00Z"}),
            json.dumps(
                {
                    "type": "transcript",
                    "text": "రాహుల్ నోట్ ప్యాడ్",
                    "segment_id": "seg_01",
                    "segment_index": 0,
                    "audio_duration_ms": 960,
                    "latency": 140,
                }
            ),
            json.dumps(
                {
                    "type": "settings_updated",
                    "config": {"language_code": "te-IN", "format": "verbatim"},
                }
            ),
        ]
    )

    async def fake_connect(url, additional_headers=None, **kwargs):
        captured_headers.update(additional_headers or {})
        return ws_session

    stt = GnaniSTTProvider(config=make_test_config(), ws_connector=fake_connect)
    events: List[STTTranscriptEvent] = []
    async for ev in stt.stream_pcm(
        async_pcm_chunks(make_synthetic_pcm()),
        language_code="te-IN",
        realtime_pace=False,
    ):
        events.append(ev)

    assert "x-partials" not in captured_headers
    assert captured_headers["x-format"] == "verbatim"  # te-IN never claims ITN
    assert [e.kind for e in events] == [
        TranscriptEventKind.CONNECTED,
        TranscriptEventKind.PROCESSING,
        TranscriptEventKind.TRANSCRIPT,
        TranscriptEventKind.SETTINGS_UPDATED,
    ]
    tr = events[2]
    assert tr.is_final is True
    assert tr.text == "రాహుల్ నోట్ ప్యాడ్"
    assert tr.segment_id == "seg_01"
    assert tr.latency_ms == 140
    assert ws_session.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_ws_final_event_named_transcript():
    ws_session = FakeWebSocketSession(
        [
            json.dumps({"type": "connected", "config": {"sample_rate": 16000}}),
            json.dumps(
                {
                    "type": "transcript",
                    "text": "open notepad",
                    "segment_id": "seg_1",
                    "segment_index": 0,
                    "audio_duration_ms": 1200,
                    "latency": 165,
                }
            ),
        ]
    )

    stt = GnaniSTTProvider(
        config=make_test_config(), ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_session)
    )
    events = [
        ev
        async for ev in stt.stream_pcm(async_pcm_chunks(make_synthetic_pcm()), realtime_pace=False)
    ]

    finals = [e for e in events if e.kind == TranscriptEventKind.FINAL]
    assert len(finals) == 1
    assert finals[0].is_final is True
    assert finals[0].text == "open notepad"
    assert finals[0].latency_ms == 165
    assert stt.is_operationally_tested is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_ws_undocumented_final_transcript_is_logged_and_ignored(caplog):
    ws_session = FakeWebSocketSession(
        [
            json.dumps({"type": "connected", "config": {"sample_rate": 16000}}),
            json.dumps(
                {
                    "type": "final_transcript",
                    "text": "undocumented event type must be ignored",
                }
            ),
            json.dumps(
                {
                    "type": "transcript",
                    "text": "documented transcript event",
                    "segment_id": "seg_99",
                    "segment_index": 1,
                    "latency": 107,
                }
            ),
        ]
    )

    stt = GnaniSTTProvider(
        config=make_test_config(), ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_session)
    )
    events = [
        ev
        async for ev in stt.stream_pcm(
            async_pcm_chunks(make_synthetic_pcm()), language_code="te-IN", realtime_pace=False
        )
    ]

    finals = [e for e in events if e.kind == TranscriptEventKind.TRANSCRIPT]
    assert len(finals) == 1
    assert finals[0].text == "documented transcript event"
    assert finals[0].segment_id == "seg_99"
    assert any("final_transcript" in r.message for r in caplog.records)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_ws_duplicate_transcript_segment_deduplicated():
    """Verify duplicate transcript frames with the same segment_id emit once."""
    ws_session = FakeWebSocketSession(
        [
            json.dumps({"type": "connected", "config": {"sample_rate": 16000}}),
            json.dumps(
                {
                    "type": "transcript",
                    "segment_id": "seg_dup_1",
                    "segment_index": 1,
                    "text": "open notepad now",
                }
            ),
            json.dumps(
                {
                    "type": "transcript",
                    "segment_id": "seg_dup_1",
                    "segment_index": 1,
                    "text": "open notepad now",
                }
            ),
        ]
    )

    stt = GnaniSTTProvider(
        config=make_test_config(), ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_session)
    )
    events = [
        ev
        async for ev in stt.stream_pcm(async_pcm_chunks(make_synthetic_pcm()), realtime_pace=False)
    ]

    finals = [e for e in events if e.kind == TranscriptEventKind.TRANSCRIPT]
    assert len(finals) == 1
    assert finals[0].text == "open notepad now"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_empty_transcript_and_empty_audio():
    # 1. Empty audio bytes rejected before network call
    stt = GnaniSTTProvider(config=make_test_config())
    with pytest.raises(VoiceInvalidAudioError) as exc_empty:
        await stt.transcribe_bytes(b"")
    assert exc_empty.value.code == "STT_EMPTY_AUDIO"

    # 2. Empty stream rejected and socket cleaned up
    ws_session = FakeWebSocketSession([json.dumps({"type": "connected"})])
    stt_ws = GnaniSTTProvider(
        config=make_test_config(), ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_session)
    )

    async def empty_gen() -> AsyncIterator[bytes]:
        if False:
            yield b""

    with pytest.raises(VoiceInvalidAudioError) as exc_stream:
        async for _ in stt_ws.stream_pcm(empty_gen()):
            pass
    assert exc_stream.value.code == "STT_EMPTY_AUDIO"
    assert ws_session.closed is True

    # 3. Server returning empty transcript string is handled cleanly
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"success": True, "request_id": "r_empty", "transcript": ""}
        )

    stt_rest = GnaniSTTProvider(
        config=make_test_config(), http_transport=httpx.MockTransport(handler)
    )
    res = await stt_rest.transcribe_bytes(make_synthetic_wav())
    assert res.success is True
    assert res.transcript == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_ws_malformed_json_and_unexpected_event_type():
    ws_session = FakeWebSocketSession(
        [
            "{not-valid-json",
            json.dumps(
                {"type": "unknown_telemetry_ping", "text": "MUST_NOT_BE_EMITTED_AS_TRANSCRIPT"}
            ),
            json.dumps({"type": "transcript", "text": "valid final text", "segment_id": "seg_ok"}),
        ]
    )

    stt = GnaniSTTProvider(
        config=make_test_config(), ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_session)
    )
    events = [
        ev
        async for ev in stt.stream_pcm(async_pcm_chunks(make_synthetic_pcm()), realtime_pace=False)
    ]

    assert len(events) == 2
    assert events[0].kind == TranscriptEventKind.ERROR
    assert "Malformed JSON" in (events[0].error_message or "")
    assert events[1].kind == TranscriptEventKind.FINAL
    assert events[1].text == "valid final text"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_auth_rate_limit_connection_timeout_and_redaction():
    # 1. 401 Auth failure with echoed secret in body -> must be redacted
    def auth_fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": f"Invalid key {SYNTHETIC_API_KEY} in X-API-Key-ID: {SYNTHETIC_API_KEY}"},
        )

    stt_401 = GnaniSTTProvider(
        config=make_test_config(), http_transport=httpx.MockTransport(auth_fail_handler)
    )
    with pytest.raises(VoiceAuthError) as exc_401:
        await stt_401.transcribe_bytes(make_synthetic_wav())
    assert exc_401.value.status_code == 401
    assert SYNTHETIC_API_KEY not in str(exc_401.value)
    assert SYNTHETIC_API_KEY not in json.dumps(exc_401.value.to_dict())
    assert "[REDACTED_SECRET]" in str(exc_401.value)

    # 2. 429 Rate limit
    def rate_limit_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "Rate limit exceeded"})

    stt_429 = GnaniSTTProvider(
        config=make_test_config(), http_transport=httpx.MockTransport(rate_limit_handler)
    )
    with pytest.raises(VoiceRateLimitError) as exc_429:
        await stt_429.transcribe_bytes(make_synthetic_wav())
    assert exc_429.value.status_code == 429

    # 3. Connection failure
    def conn_fail_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Failed connecting with key {SYNTHETIC_API_KEY}")

    stt_conn = GnaniSTTProvider(
        config=make_test_config(), http_transport=httpx.MockTransport(conn_fail_handler)
    )
    with pytest.raises(VoiceConnectionError) as exc_conn:
        await stt_conn.transcribe_bytes(make_synthetic_wav())
    assert SYNTHETIC_API_KEY not in str(exc_conn.value)

    # 4. Timeout
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Read timed out")

    stt_timeout = GnaniSTTProvider(
        config=make_test_config(), http_transport=httpx.MockTransport(timeout_handler)
    )
    with pytest.raises(VoiceTimeoutError):
        await stt_timeout.transcribe_bytes(make_synthetic_wav())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_ws_resource_cleanup_on_midstream_error():
    ws_session = FakeWebSocketSession(
        incoming_frames=[json.dumps({"type": "connected"})],
        raise_on_recv=RuntimeError(f"Socket dropped for {SYNTHETIC_API_KEY}"),
    )
    stt = GnaniSTTProvider(
        config=make_test_config(), ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_session)
    )

    with pytest.raises(RuntimeError):
        async for _ in stt.stream_pcm(async_pcm_chunks(make_synthetic_pcm())):
            pass

    assert ws_session.closed is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stt_rest_single_language_transcript_parsing():
    def single_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "request_id": "req_single_01",
                "model": "Prisma v2.5",
                "transcript": "రాహుల్ నోట్ ప్యాడ్ ఓపెన్ చేశాను",
                "processing_time": 0.12,
                "end_to_end_latency": 0.15,
            },
        )

    stt = GnaniSTTProvider(
        config=make_test_config(), http_transport=httpx.MockTransport(single_handler)
    )
    res = await stt.transcribe_bytes(
        make_synthetic_wav(),
        language_code="te-IN",
    )
    assert res.success is True
    assert res.language_code == "te-IN"
    assert res.transcript == "రాహుల్ నోట్ ప్యాడ్ ఓపెన్ చేశాను"
    assert not hasattr(res, "multi_lang_transcripts")


@pytest.mark.unit
def test_phase2_bias_list_sanitizer_indic_vowel_signs_and_dropped_logging(caplog):
    """Verify sanitize_bias_list accepts Hindi/Tamil/Telugu vowel signs (Mn/Mc) and logs drops."""
    candidates = [
        "नोटपैड",  # Hindi with o, ai vowel signs (Mc/Mn)
        "रुको",  # Hindi with u, o vowel signs
        "நோட்பேட்",  # Tamil with o, e vowel signs and pulli (Mc/Mn)
        "நில்லு",  # Tamil with i, u vowel signs and pulli
        "ఆగు",  # Telugu with u vowel sign (Mc)
        "చెయ్యకు",  # Telugu with e, virama, u vowel signs
        "Notepad",  # Valid English
        "HDFC2",  # Invalid: contains digit
        "loan number",  # Invalid: contains space
        "co-op",  # Invalid: contains hyphen
        "@brand!",  # Invalid: punctuation/symbols
        "   ",  # Invalid: empty
    ]
    accepted, dropped = sanitize_bias_list(candidates)
    assert "नोटपैड" in accepted
    assert "रुको" in accepted
    assert "நோட்பேட்" in accepted
    assert "நில்லு" in accepted
    assert "ఆగు" in accepted
    assert "చెయ్యకు" in accepted
    assert "Notepad" in accepted
    assert len(accepted) == 7

    dropped_entries = [d["entry"] for d in dropped]
    assert "HDFC2" in dropped_entries
    assert "loan number" in dropped_entries
    assert "co-op" in dropped_entries
    assert "@brand!" in dropped_entries
    assert len(dropped) == 5
    assert any("HDFC2" in rec.message for rec in caplog.records)


@pytest.mark.unit
def test_phase2_itn_format_rules_hi_en_only_and_verbatim_with_bias_list():
    """Verify format=transcribe (ITN) is used ONLY for hi-IN/en-IN when bias_list is empty."""
    assert resolve_stt_output_format("hi-IN", has_bias_list=False) == "transcribe"
    assert resolve_stt_output_format("en-IN", has_bias_list=False) == "transcribe"
    # When bias_list is present, format MUST be verbatim even for hi-IN and en-IN
    assert resolve_stt_output_format("hi-IN", has_bias_list=True) == "verbatim"
    assert resolve_stt_output_format("en-IN", has_bias_list=True) == "verbatim"
    # For ta-IN and te-IN, ITN is never claimed or used
    assert resolve_stt_output_format("ta-IN", has_bias_list=False) == "verbatim"
    assert resolve_stt_output_format("te-IN", has_bias_list=False) == "verbatim"
    assert (
        resolve_stt_output_format("ta-IN", requested_format="transcribe", has_bias_list=False)
        == "verbatim"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase2_frame_sizing_and_monotonic_drift_correcting_pacing():
    """Verify 1,024-byte PCM framing and monotonic clock pacing (32ms @ 16kHz, 64ms @ 8kHz)."""
    sent_timestamps: List[float] = []

    class TimingFakeWS(FakeWebSocketSession):
        async def send(self, data: Any) -> None:
            sent_timestamps.append(time.monotonic())
            await super().send(data)

    # 3 frames (3,072 bytes) + 100 trailing bytes -> should emit 4 frames of exactly 1,024 bytes
    raw_pcm = b"\x01\x00" * (512 * 3 + 50)
    ws_16k = TimingFakeWS(
        [
            json.dumps({"type": "connected"}),
            json.dumps({"type": "transcript", "text": "test", "segment_id": "s1"}),
        ]
    )
    stt = GnaniSTTProvider(
        config=make_test_config(), ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_16k)
    )
    async for _ in stt.stream_pcm(
        async_pcm_chunks(raw_pcm, chunk_size=700),
        sample_rate=16000,
        realtime_pace=True,
    ):
        pass

    assert len(ws_16k.sent_frames) == 4
    assert all(len(f) == 1024 for f in ws_16k.sent_frames)
    # 4 frames paced at 32ms intervals (0, 32ms, 64ms, 96ms) -> total span >= ~85ms
    total_span_16k = sent_timestamps[-1] - sent_timestamps[0]
    assert total_span_16k >= 0.080


@pytest.mark.unit
def test_phase2_segment_stitcher_and_vad_profile_switching():
    """Verify single-connection SegmentStitcher and config-flagged VADProfileController."""
    stitcher = SegmentStitcher(min_silence_ms=350, stitch_window_ms=650)
    t1, joined1 = stitcher.add_segment(
        "नोटपैड खोलो", segment_id="seg_1", latency_ms=150, arrival_monotonic=10.0
    )
    assert t1 == "नोटपैड खोलो"
    assert joined1 is False

    # Second segment arrives 400ms later (<= 650ms window) -> stitched!
    t2, joined2 = stitcher.add_segment(
        "और यह लिखो", segment_id="seg_2", latency_ms=160, arrival_monotonic=10.4
    )
    assert t2 == "नोटपैड खोलो और यह लिखो"
    assert joined2 is True

    flushed = stitcher.flush()
    assert flushed["text"] == "नोटपैड खोलो और यह लिखो"
    assert flushed["segment_ids"] == ["seg_1", "seg_2"]
    assert flushed["min_silence_ms"] == 350
    assert flushed["stitch_window_ms"] == 650

    # Default config: reconnect_on_profile_switch=False -> never reconnects
    cfg_default = make_test_config()
    ctrl_default = VADProfileController(cfg_default)
    assert ctrl_default.switch_profile(VADProfile.STOP) is False
    assert ctrl_default.reconnect_count == 0

    # Opt-in config flag for Phase 5 comparison: reconnect_on_profile_switch=True
    cfg_reconn = make_test_config(reconnect_on_profile_switch=True)
    ctrl_reconn = VADProfileController(cfg_reconn)
    assert ctrl_reconn.switch_profile(VADProfile.STOP) is True
    assert ctrl_reconn.reconnect_count == 1
    assert ctrl_reconn.headers_for_profile(VADProfile.STOP)["x-min-silence-ms"] == "300"
    assert ctrl_reconn.headers_for_profile(VADProfile.COMMAND)["x-min-silence-ms"] == "600"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phase2_tts_sse_sha256_on_disk_cache(tmp_path):
    """Verify Timbre v3.0 SSE synthesis caches WAV on disk by sha256(text+voice+model)."""
    sentinel_wav = make_synthetic_wav(duration_ms=200, sample_rate=24000, sentinel_sizes=True)
    b64_chunk = base64.b64encode(sentinel_wav).decode("ascii")
    sse_payload = (
        "event: start\n"
        'data: {"status": "streaming_started"}\n\n'
        "event: chunk\n"
        f'data: {{"chunk_index": 1, "audio": "{b64_chunk}", "is_final": false}}\n\n'
        "event: complete\n"
        'data: {"chunk_index": 2, "audio": "", "is_final": true}\n\n'
    )
    http_calls = {"count": 0}

    def counting_sse_handler(request: httpx.Request) -> httpx.Response:
        http_calls["count"] += 1
        return httpx.Response(200, text=sse_payload, headers={"Content-Type": "text/event-stream"})

    tts = GnaniTTSProvider(
        config=make_test_config(),
        http_transport=httpx.MockTransport(counting_sse_handler),
        cache_dir=tmp_path / "tts_cache",
    )
    req = TTSRequest(
        text="Opened notepad. <pauses> What should I write?",
        voice="Shivani",
        model="timbre-v3.0",
        language="en-IN",
    )

    res1 = await tts.synthesize_buffered_sse(req, use_cache=True)
    assert http_calls["count"] == 1
    assert tts.cache_misses == 1
    assert tts.cache_hits == 0
    assert len(res1.audio_bytes) > 44

    # Second call with identical (text, voice, model) hits on-disk cache with 0 HTTP calls!
    res2 = await tts.synthesize_buffered_sse(req, use_cache=True)
    assert http_calls["count"] == 1
    assert tts.cache_hits == 1
    assert res2.audio_bytes == res1.audio_bytes


# ===========================================================================
# 2. GnaniTTSProvider & WAV Container Unit Tests (Timbre v2.5 & v3.0)
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tts_missing_api_key():
    tts = GnaniTTSProvider(config=make_test_config(api_key=""))
    with pytest.raises(VoiceConfigError) as exc_info:
        await tts.synthesize(TTSRequest(text="Hello"))
    assert exc_info.value.code == "VOICE_KEY_MISSING"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tts_valid_complete_wav_and_raw_pcm_responses():
    valid_wav = make_synthetic_wav(duration_ms=250, sample_rate=24000, sentinel_sizes=False)
    raw_pcm = make_synthetic_pcm(duration_ms=250, sample_rate=16000)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if body["audio_config"]["container"] == "raw":
            return httpx.Response(
                200, content=raw_pcm, headers={"Content-Type": "application/octet-stream"}
            )
        return httpx.Response(200, content=valid_wav, headers={"Content-Type": "audio/wav"})

    tts = GnaniTTSProvider(config=make_test_config(), http_transport=httpx.MockTransport(handler))

    # 1. Standard WAV response
    wav_res = await tts.synthesize(
        TTSRequest(text="Notepad is open.", voice="Shivani", model="timbre-v3.0", language="en-IN")
    )
    assert wav_res.container == "wav"
    assert wav_res.mime_type == "audio/wav"
    assert wav_res.sample_rate == 24000
    assert wav_res.wav_header_repaired is False
    assert wav_res.duration_seconds == pytest.approx(0.25, rel=1e-2)

    # 2. Documented Raw PCM response (must never be mislabeled as WAV)
    pcm_res = await tts.synthesize(
        TTSRequest(
            text="Notepad is open.",
            voice="Kaveri",
            model="timbre-v2.5",
            language="en-IN",
            audio_config=TTSAudioConfig(sample_rate=16000, container="raw", encoding="linear_pcm"),
        )
    )
    assert pcm_res.container == "raw"
    assert pcm_res.mime_type == "application/octet-stream"
    assert pcm_res.audio_bytes == raw_pcm
    assert pcm_res.duration_seconds == pytest.approx(0.25, rel=1e-2)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tts_wav_sentinel_0xffffffff_and_non44_junk_subchunk_repaired():
    """Verify RIFF chunk-walking parser repairs 0xFFFFFFFF sizes with JUNK subchunk."""
    sentinel_wav = make_synthetic_wav(
        duration_ms=500,
        sample_rate=16000,
        sentinel_sizes=True,
        extra_junk_chunk=True,
    )

    # Confirm raw fixture has 0xFFFFFFFF in RIFF size
    raw_meta = parse_wav_container(sentinel_wav)
    assert raw_meta.had_sentinel_size is True
    assert raw_meta.data_payload_offset > 44  # Shifted by JUNK subchunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sentinel_wav, headers={"Content-Type": "audio/wav"})

    tts = GnaniTTSProvider(config=make_test_config(), http_transport=httpx.MockTransport(handler))
    res = await tts.synthesize(
        TTSRequest(
            text="Rahul, Notepad open chesanu.",
            voice="Suhana",
            model="timbre-v2.5",
            language="te-IN",
            audio_config=TTSAudioConfig(sample_rate=16000, container="wav"),
        )
    )

    assert res.wav_header_repaired is True
    assert res.duration_seconds == pytest.approx(0.5, rel=1e-2)

    # Verify repaired bytes parse with standard library wave module to exact frame count
    repaired_meta = parse_wav_container(res.audio_bytes)
    assert repaired_meta.had_sentinel_size is False
    assert repaired_meta.riff_size_field == len(res.audio_bytes) - 8
    assert repaired_meta.data_size_field == len(res.audio_bytes) - repaired_meta.data_payload_offset

    with wave.open(io.BytesIO(res.audio_bytes), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getnframes() == 8000  # 0.5s * 16000 Hz


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tts_sse_streaming_and_buffered_wav_normalization():
    sentinel_wav = make_synthetic_wav(duration_ms=300, sample_rate=24000, sentinel_sizes=True)
    part1 = base64.b64encode(sentinel_wav[:120]).decode("ascii")
    part2 = base64.b64encode(sentinel_wav[120:]).decode("ascii")

    sse_payload = (
        "event: start\n"
        'data: {"status": "streaming_started"}\n\n'
        "event: chunk\n"
        f'data: {{"chunk_index": 1, "audio": "{part1}", "is_final": false}}\n\n'
        "event: chunk\n"
        f'data: {{"chunk_index": 2, "audio": "{part2}", "is_final": false}}\n\n'
        "event: complete\n"
        'data: {"chunk_index": 3, "audio": "", "is_final": true}\n\n'
    )

    def sse_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse_payload, headers={"Content-Type": "text/event-stream"})

    tts = GnaniTTSProvider(
        config=make_test_config(), http_transport=httpx.MockTransport(sse_handler)
    )
    req = TTSRequest(
        text="Testing SSE stream", voice="Shivani", model="timbre-v3.0", language="en-IN"
    )

    res = await tts.synthesize_buffered_sse(req)
    assert res.chunks_count == 2
    assert res.wav_header_repaired is True
    assert res.duration_seconds == pytest.approx(0.3, rel=1e-2)
    assert tts.is_operationally_tested is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tts_validation_errors_interrupted_stream_oversized_and_redaction():
    # 1. Empty text & model/voice mismatch rejected locally
    tts = GnaniTTSProvider(config=make_test_config())
    with pytest.raises(VoiceConfigError):
        await tts.synthesize(TTSRequest(text="   "))

    with pytest.raises(VoiceConfigError) as exc_mismatch:
        await tts.synthesize(TTSRequest(text="Hello", voice="Nalini", model="timbre-v3.0"))
    assert exc_mismatch.value.code == "TTS_VOICE_MODEL_MISMATCH"

    # 2. Invalid content-type / non-WAV payload on 200 OK
    def bad_audio_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"NOT_A_WAV_FILE_PAYLOAD", headers={"Content-Type": "audio/wav"}
        )

    tts_bad = GnaniTTSProvider(
        config=make_test_config(), http_transport=httpx.MockTransport(bad_audio_handler)
    )
    with pytest.raises(VoiceInvalidAudioError):
        await tts_bad.synthesize(TTSRequest(text="Hello"))

    # 3. Interrupted SSE stream (missing 'complete' event)
    b64_frag = base64.b64encode(b"1234").decode()
    interrupted_sse = (
        "event: start\n"
        'data: {"status": "streaming_started"}\n\n'
        "event: chunk\n"
        f'data: {{"chunk_index": 1, "audio": "{b64_frag}", "is_final": false}}\n\n'
    )
    tts_interrupted = GnaniTTSProvider(
        config=make_test_config(),
        http_transport=httpx.MockTransport(lambda req: httpx.Response(200, text=interrupted_sse)),
    )
    with pytest.raises(VoiceConnectionError) as exc_int:
        await tts_interrupted.synthesize_buffered_sse(TTSRequest(text="Hello"))
    assert exc_int.value.code == "TTS_SSE_INTERRUPTED"

    # 4. Oversized response rejected
    small_limit_cfg = make_test_config(max_tts_response_bytes=4096)
    huge_wav = make_synthetic_wav(duration_ms=500, sample_rate=16000)  # >16,000 bytes
    tts_oversize = GnaniTTSProvider(
        config=small_limit_cfg,
        http_transport=httpx.MockTransport(lambda req: httpx.Response(200, content=huge_wav)),
    )
    with pytest.raises(VoiceInvalidAudioError) as exc_over:
        await tts_oversize.synthesize(TTSRequest(text="Hello"))
    assert exc_over.value.code == "TTS_RESPONSE_TOO_LARGE"

    # 5. Auth 401, Rate limit 429, Timeout, and Secret Redaction
    def tts_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"Unauthorized key={SYNTHETIC_API_KEY}")

    tts_auth = GnaniTTSProvider(
        config=make_test_config(), http_transport=httpx.MockTransport(tts_401)
    )
    with pytest.raises(VoiceAuthError) as exc_t401:
        await tts_auth.synthesize(TTSRequest(text="Hello"))
    assert SYNTHETIC_API_KEY not in str(exc_t401.value)
    assert "[REDACTED_SECRET]" in str(exc_t401.value)

    tts_429 = GnaniTTSProvider(
        config=make_test_config(),
        http_transport=httpx.MockTransport(
            lambda req: httpx.Response(429, text="Too many requests")
        ),
    )
    with pytest.raises(VoiceRateLimitError):
        await tts_429.synthesize(TTSRequest(text="Hello"))

    def tts_timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("TTS timed out")

    tts_to = GnaniTTSProvider(
        config=make_test_config(),
        http_transport=httpx.MockTransport(tts_timeout_handler),
    )
    with pytest.raises(VoiceTimeoutError):
        await tts_to.synthesize(TTSRequest(text="Hello"))


# ===========================================================================
# 3. CapabilityRegistry Unit Tests
# ===========================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capability_registry_states_and_zero_unintended_network_calls():
    network_calls = 0

    def counting_handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_calls
        network_calls += 1
        return httpx.Response(200, json={"data": [], "pagination": {"has_more": False}})

    transport = httpx.MockTransport(counting_handler)

    # 1. No providers registered -> UNAVAILABLE
    empty_reg = CapabilityRegistry()
    assert empty_reg.get_stt_report().status == CapabilityStatus.UNAVAILABLE
    assert empty_reg.get_tts_report().status == CapabilityStatus.UNAVAILABLE

    # 2. Unconfigured providers -> UNCONFIGURED
    unconf_cfg = make_test_config(api_key="")
    unconf_reg = CapabilityRegistry(
        stt_provider=GnaniSTTProvider(config=unconf_cfg, http_transport=transport),
        tts_provider=GnaniTTSProvider(config=unconf_cfg, http_transport=transport),
    )
    assert unconf_reg.get_stt_report().status == CapabilityStatus.UNCONFIGURED
    assert unconf_reg.get_tts_report().status == CapabilityStatus.UNCONFIGURED
    assert network_calls == 0

    # 3. Configured providers -> CONFIGURED (zero network calls on init or to_dict)
    conf_cfg = make_test_config(api_key=SYNTHETIC_API_KEY)
    stt = GnaniSTTProvider(config=conf_cfg, http_transport=transport)
    tts = GnaniTTSProvider(config=conf_cfg, http_transport=transport)
    reg = CapabilityRegistry(stt_provider=stt, tts_provider=tts)

    snap = reg.to_dict()
    assert snap["stt"]["status"] == CapabilityStatus.CONFIGURED
    assert snap["tts"]["status"] == CapabilityStatus.CONFIGURED
    assert network_calls == 0
    assert SYNTHETIC_API_KEY not in json.dumps(snap)

    # 4. Explicit non-destructive connectivity check -> REACHABLE
    verified_snap = await reg.verify_connectivity()
    assert network_calls == 2
    assert verified_snap["stt"]["status"] == CapabilityStatus.REACHABLE
    assert verified_snap["tts"]["status"] == CapabilityStatus.REACHABLE

    # 5. Operational test transition -> OPERATIONALLY_TESTED
    reg.mark_stt_operationally_tested()
    assert reg.get_stt_report().status == CapabilityStatus.OPERATIONALLY_TESTED
    assert reg.get_tts_report().status == CapabilityStatus.REACHABLE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capability_registry_independent_stt_and_tts_failure_and_redaction():
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"Invalid key {SYNTHETIC_API_KEY}")

    cfg = make_test_config(api_key=SYNTHETIC_API_KEY)

    # Case A: STT reachable while TTS fails health check
    reg_a = CapabilityRegistry(
        stt_provider=GnaniSTTProvider(config=cfg, http_transport=httpx.MockTransport(ok_handler)),
        tts_provider=GnaniTTSProvider(config=cfg, http_transport=httpx.MockTransport(fail_handler)),
    )
    snap_a = await reg_a.verify_connectivity()
    assert snap_a["stt"]["status"] == CapabilityStatus.REACHABLE
    assert snap_a["tts"]["status"] == CapabilityStatus.UNAVAILABLE
    assert SYNTHETIC_API_KEY not in json.dumps(snap_a)
    assert "[REDACTED_SECRET]" in snap_a["tts"]["reason"]

    # Case B: TTS reachable while STT fails health check
    reg_b = CapabilityRegistry(
        stt_provider=GnaniSTTProvider(config=cfg, http_transport=httpx.MockTransport(fail_handler)),
        tts_provider=GnaniTTSProvider(config=cfg, http_transport=httpx.MockTransport(ok_handler)),
    )
    snap_b = await reg_b.verify_connectivity()
    assert snap_b["stt"]["status"] == CapabilityStatus.UNAVAILABLE
    assert snap_b["tts"]["status"] == CapabilityStatus.REACHABLE
    assert SYNTHETIC_API_KEY not in json.dumps(snap_b)


# ===========================================================================
# 4. Explicit Opt-In Live Gnani API Test (Skipped by default)
# ===========================================================================


@pytest.mark.skipif(
    os.environ.get("WINAI_RUN_LIVE_GNANI_TESTS") != "1",
    reason="Live Gnani API tests require explicit WINAI_RUN_LIVE_GNANI_TESTS=1 opt-in.",
)
@pytest.mark.asyncio
async def test_live_gnani_opt_in_roundtrip():
    """Minimal opt-in live verification of CapabilityRegistry, Timbre TTS, and Prisma STT."""
    cfg = GnaniConfig.from_env_or_vault(allow_vault_lookup=True)
    if not cfg.is_configured:
        pytest.skip("Gnani API key not configured in environment or DPAPI vault.")

    stt = GnaniSTTProvider(config=cfg)
    tts = GnaniTTSProvider(config=cfg)
    reg = CapabilityRegistry(stt_provider=stt, tts_provider=tts)

    # 1. Non-destructive connectivity check
    conn = await reg.verify_connectivity()
    assert conn["stt"]["status"] == CapabilityStatus.REACHABLE
    assert conn["tts"]["status"] == CapabilityStatus.REACHABLE

    # 2. Minimal TTS synthesis with WAV header normalization check
    tts_res = await tts.synthesize(
        TTSRequest(
            text="Notepad is open.",
            voice="Kaveri",
            model="timbre-v2.5",
            language="en-IN",
            audio_config=TTSAudioConfig(sample_rate=16000, container="wav"),
        )
    )
    assert tts_res.size_bytes > 1000
    assert tts_res.duration_seconds is not None and tts_res.duration_seconds > 0.2
    meta = parse_wav_container(tts_res.audio_bytes)
    assert meta.had_sentinel_size is False

    # 3. Minimal STT transcription of synthesized audio
    stt_res = await stt.transcribe_bytes(tts_res.audio_bytes, language_code="en-IN")
    assert stt_res.success is True
    assert "notepad" in stt_res.transcript.lower()
    assert reg.get_stt_report().status == CapabilityStatus.OPERATIONALLY_TESTED
    assert reg.get_tts_report().status == CapabilityStatus.OPERATIONALLY_TESTED
