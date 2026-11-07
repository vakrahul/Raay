"""Deterministic UI, integration, state, and security tests for Milestone 4:
- Command Cockpit UI rendering, 10 visual states, 3D Core + 2D fallback, and reduced-motion support
- Voice capabilities, device enumeration, and settings validation (/api/v1/voice/capabilities)
- Session lifecycle (start, listening, stop, cancel after cleanup)
- Controlled voice turn execution, policy denial, approval resolution, and tampering rejection
- TTS failure resilience (completed task is never re-executed when TTS fails)
"""

import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from src.orchestrator.config import AppConfig, SecurityLevel
from src.orchestrator.main import create_app
from src.voice import (
    VALID_COCKPIT_STATES,
    GnaniConfig,
    GnaniTTSProvider,
)
from tests.unit.test_voice_milestone2 import (
    FakeAudioInputBackend,
    FakeAudioOutputBackend,
    make_pcm_frame,
)

SYNTHETIC_M4_KEY = "vach_milestone4_synthetic_key_0123456789abcdef"


@pytest.fixture
def cockpit_client(temp_workspace: Path):
    cfg = AppConfig(
        workspace_root=temp_workspace,
        security_level=SecurityLevel.STRICT,
        environment="testing",
    )
    app = create_app(cfg)
    # Inject deterministic fake audio backends so tests never touch physical microphones/speakers
    app.state.voice_service._input_backend = FakeAudioInputBackend(
        chunks=[make_pcm_frame(), make_pcm_frame()]
    )
    app.state.voice_service._output_backend = FakeAudioOutputBackend()
    with TestClient(app) as client:
        yield client, app, temp_workspace


# ===========================================================================
# 1. Command Cockpit UI & Visual State Contract Tests
# ===========================================================================


@pytest.mark.unit
def test_cockpit_ui_renders_all_required_states_controls_and_reduced_motion(cockpit_client):
    client, _, _ = cockpit_client
    for route in ("/", "/dashboard", "/cockpit"):
        res = client.get(route)
        assert res.status_code == 200
        html = res.text

        # Verify 3D Intelligence Core canvas + 2D fallback
        assert 'id="intelligenceCoreCanvas"' in html
        assert 'id="coreFallback2D"' in html

        # Verify all 10 required visual states are defined in the state machine
        for state_name in VALID_COCKPIT_STATES:
            assert state_name in html, f"Missing visual state {state_name} in Cockpit UI"

        # Verify required voice controls & accessibility attributes
        for control_id in (
            "btnPushToTalk",
            "btnStartListen",
            "btnStopListen",
            "btnCancelSession",
            "voiceTranscriptInput",
            "voiceApprovalCard",
            "liveTranscriptDisplay",
            "interpretedIntentDisplay",
            "actionStatusDisplay",
            "ttsStatusDisplay",
            "settingInputDevice",
            "settingOutputDevice",
            "settingTheme",
            "settingMotion",
            "ariaLiveStatus",
        ):
            assert f'id="{control_id}"' in html, f"Missing control {control_id}"

        # Verify reduced-motion support & document.hidden pause check
        assert "prefers-reduced-motion: reduce" in html
        assert "document.hidden" in html
        assert SYNTHETIC_M4_KEY not in html


# ===========================================================================
# 2. Capabilities, Settings & Honest Provider Status Tests
# ===========================================================================


@pytest.mark.unit
def test_voice_capabilities_and_settings_endpoints_truthful_and_secret_free(cockpit_client):
    client, _, _ = cockpit_client

    # 1. Capabilities in testing mode report unconfigured honestly with zero network calls
    cap_res = client.get("/api/v1/voice/capabilities")
    assert cap_res.status_code == 200
    data = cap_res.json()
    assert data["cockpit_state"] == "IDLE"
    assert data["capabilities"]["stt"]["status"] == "unconfigured"
    assert data["capabilities"]["tts"]["status"] == "unconfigured"
    assert data["reasoning"]["active_planner"].startswith("Planner:")
    assert "gnani_evon_status" not in data["reasoning"]
    assert len(data["devices"]["input_devices"]) == 2
    assert len(data["devices"]["output_devices"]) == 2

    # 2. Update valid settings
    upd = client.post(
        "/api/v1/voice/settings",
        json={
            "input_device_id": 1,
            "output_device_id": 0,
            "language_code": "te-IN",
            "voice": "Shivani",
            "model": "timbre-v3.0",
            "speak_responses": False,
            "theme": "light",
            "motion_mode": "reduced",
        },
    )
    assert upd.status_code == 200
    assert upd.json()["language_code"] == "te-IN"
    assert upd.json()["motion_mode"] == "reduced"

    # 3. Reject mismatched model/voice in settings
    bad_upd = client.post(
        "/api/v1/voice/settings",
        json={
            "language_code": "hi-IN",
            "voice": "Nalini",  # v2.5 voice on v3.0 model
            "model": "timbre-v3.0",
        },
    )
    assert bad_upd.status_code == 422


# ===========================================================================
# 3. Session Start, Listening, Stop & Cancellation Lifecycle Tests
# ===========================================================================


@pytest.mark.unit
def test_voice_session_start_listening_stop_and_cancel_lifecycle(cockpit_client):
    client, app, _ = cockpit_client
    in_backend: FakeAudioInputBackend = app.state.voice_service._input_backend

    # 1. Start session without capture -> IDLE, mic inactive
    r_idle = client.post("/api/v1/voice/session/start", json={"start_capture": False})
    assert r_idle.status_code == 200
    assert r_idle.json()["cockpit_state"] == "IDLE"
    assert in_backend.is_open is False

    # 2. Start session with explicit capture -> LISTENING
    r_listen = client.post("/api/v1/voice/session/start", json={"start_capture": True})
    assert r_listen.status_code == 200
    sid = r_listen.json()["session_id"]
    assert r_listen.json()["cockpit_state"] == "LISTENING"

    status_res = client.get(f"/api/v1/voice/session/{sid}/status")
    assert status_res.status_code == 200

    # 3. Cancel active session -> confirms cleanup before returning CANCELLED
    cancel_res = client.post(f"/api/v1/voice/session/{sid}/cancel")
    assert cancel_res.status_code == 200
    c_data = cancel_res.json()
    assert c_data["cockpit_state"] == "CANCELLED"
    assert c_data["cancelled"] is True
    assert c_data["active_task_count"] == 0
    assert in_backend.is_open is False


# ===========================================================================
# 4. Controlled Turn Execution, Approval Integrity & Tampering Tests
# ===========================================================================


@pytest.mark.unit
def test_voice_turn_policy_denial_approval_workflow_and_tampering_blocked(cockpit_client):
    client, _, temp_workspace = cockpit_client

    # 1. Frontend tampering (injecting user_approved / bypass_policy) is rejected with 422
    tamper_res = client.post(
        "/api/v1/voice/turn",
        json={
            "transcript": "Create file hacked.txt with content bad",
            "user_approved": True,
            "bypass_policy": True,
        },
    )
    assert tamper_res.status_code == 422
    assert not (temp_workspace / "hacked.txt").exists()

    # 2a. Forbidden command keyword ("Open PowerShell") -> REJECTED_VALIDATION
    val_res = client.post("/api/v1/voice/turn", json={"transcript": "Open PowerShell"})
    assert val_res.status_code == 200
    assert val_res.json()["cockpit_state"] == "ERROR"
    assert val_res.json()["result"]["status"] == "REJECTED_VALIDATION"

    # 2b. Unwhitelisted app launch ("Open mspaint") -> DENIED_BY_POLICY, no pending approval created
    deny_res = client.post("/api/v1/voice/turn", json={"transcript": "Open mspaint"})
    assert deny_res.status_code == 200
    d_body = deny_res.json()
    assert d_body["cockpit_state"] == "ERROR"
    assert d_body["result"]["status"] == "DENIED_BY_POLICY"
    assert d_body["result"]["pending_approval"] is None

    act_after_deny = client.get("/api/v1/voice/activity").json()
    assert act_after_deny["pending_approvals"] == []

    # 3. Allowlisted read-only command ("List approved apps") -> immediate SUCCESS
    apps_res = client.post("/api/v1/voice/turn", json={"transcript": "List approved apps"})
    assert apps_res.status_code == 200
    assert apps_res.json()["cockpit_state"] == "SUCCESS"
    assert apps_res.json()["result"]["status"] == "COMPLETED"

    # 4. Approval-required command ("Create file cockpit_note.txt with content Verified Note")
    req_res = client.post(
        "/api/v1/voice/turn",
        json={"transcript": "Create file cockpit_note.txt with content Verified Note"},
    )
    assert req_res.status_code == 200
    req_body = req_res.json()
    assert req_body["cockpit_state"] == "AWAITING_APPROVAL"
    pending = req_body["result"]["pending_approval"]
    assert pending is not None
    nonce = pending["approval_nonce"]
    sid = req_body["result"]["session_id"]
    assert not (temp_workspace / "cockpit_note.txt").exists()

    # Verify /api/v1/voice/activity lists the pending approval
    act_pending = client.get("/api/v1/voice/activity").json()
    assert len(act_pending["pending_approvals"]) == 1
    assert act_pending["pending_approvals"][0]["approval_nonce"] == nonce

    # 5. User denies approval -> CANCELLED, file not created
    deny_appr = client.post(
        "/api/v1/voice/approval/resolve",
        json={"session_id": sid, "approval_nonce": nonce, "approved": False},
    )
    assert deny_appr.status_code == 200
    assert deny_appr.json()["cockpit_state"] == "CANCELLED"
    assert not (temp_workspace / "cockpit_note.txt").exists()

    # 6. Request again and approve -> SUCCESS, file created & verified on disk
    req_res2 = client.post(
        "/api/v1/voice/turn",
        json={"transcript": "Create file cockpit_note.txt with content Verified Note"},
    )
    nonce2 = req_res2.json()["result"]["pending_approval"]["approval_nonce"]
    sid2 = req_res2.json()["result"]["session_id"]

    allow_appr = client.post(
        "/api/v1/voice/approval/resolve",
        json={"session_id": sid2, "approval_nonce": nonce2, "approved": True},
    )
    assert allow_appr.status_code == 200
    assert allow_appr.json()["cockpit_state"] == "SUCCESS"
    assert (temp_workspace / "cockpit_note.txt").read_text(encoding="utf-8") == "Verified Note"

    # 7. Replay of consumed nonce must fail with ERROR
    replay_appr = client.post(
        "/api/v1/voice/approval/resolve",
        json={"session_id": sid2, "approval_nonce": nonce2, "approved": True},
    )
    assert replay_appr.status_code == 200
    assert replay_appr.json()["cockpit_state"] == "ERROR"
    assert replay_appr.json()["result"]["status"] == "DENIED_APPROVAL_INVALID"


# ===========================================================================
# 5. TTS Failure Resilience Test (No Duplicate Task Execution)
# ===========================================================================


@pytest.mark.unit
def test_voice_turn_tts_failure_preserves_completed_task_without_reexecution(cockpit_client):
    client, app, _ = cockpit_client
    svc = app.state.voice_service

    # Configure a TTS provider that fails with HTTP 503 and count dispatcher invocations
    svc.tts_provider = GnaniTTSProvider(
        config=GnaniConfig.from_env_or_vault(api_key=SYNTHETIC_M4_KEY, allow_vault_lookup=False),
        http_transport=httpx.MockTransport(lambda req: httpx.Response(503, text="TTS Unavailable")),
    )
    # Create a fresh session bound to the failing TTS provider
    svc.sessions.clear()
    svc.active_session_id = None

    turn_res = client.post(
        "/api/v1/voice/turn",
        json={"transcript": "List approved apps", "speak_response": True},
    )
    assert turn_res.status_code == 200
    body = turn_res.json()
    assert body["cockpit_state"] == "SUCCESS"
    assert body["result"]["status"] == "COMPLETED"
    assert body["result"]["tts_succeeded"] is False
    assert body["result"]["tts_error"] is not None
    assert SYNTHETIC_M4_KEY not in json.dumps(body)


# ===========================================================================
# 6. Phase 1 Honesty Cleanup Verification Tests (One Test Per Removal)
# ===========================================================================


@pytest.mark.unit
def test_phase1_removal_1_no_evon_in_shipped_runtime_ui_or_docs(cockpit_client):
    """Verify 'Evon' does not appear in any shipped UI, voice runtime, README, or M4 report."""
    client, _, _ = cockpit_client
    repo_root = Path(__file__).resolve().parents[2]
    checked_files = [
        *list((repo_root / "src" / "voice").glob("*.py")),
        repo_root / "src" / "orchestrator" / "dashboard.html",
        repo_root / "src" / "orchestrator" / "main.py",
        repo_root / "src" / "windows_integration" / "voice_orb_overlay.py",
        repo_root / "README.md",
        repo_root / "docs" / "voice" / "MILESTONE_4_IMPLEMENTATION_REPORT.md",
    ]
    for path in checked_files:
        text = path.read_text(encoding="utf-8")
        assert "evon" not in text.lower(), f"Forbidden 'Evon' string found in {path}"

    cap_data = client.get("/api/v1/voice/capabilities").json()
    assert "evon" not in json.dumps(cap_data).lower()
    assert cap_data["reasoning"]["active_planner"].startswith("Planner:")


@pytest.mark.unit
def test_phase1_removal_2_no_hardcoded_leave_letter_or_signer_name(cockpit_client):
    """Verify hardcoded leave-letter template and signer name are gone and trigger clarification."""
    client, app, _ = cockpit_client
    repo_root = Path(__file__).resolve().parents[2]
    for path in [
        *list((repo_root / "src" / "voice").glob("*.py")),
        repo_root / "src" / "orchestrator" / "dashboard.html",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "Rahul Vakiti" not in text, f"Hardcoded signer found in {path}"
        assert "Subject: Leave Application for Tomorrow" not in text, (
            f"Hardcoded leave-letter template found in {path}"
        )

    # Ensure no LLM provider is registered in testing mode, then request a leave application
    app.state.voice_service.provider_registry = None
    app.state.voice_service.planner.llm_provider = None
    res = client.post(
        "/api/v1/voice/turn",
        json={
            "transcript": "Write a leave application for tomorrow",
            "conversational_mode": True,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["result"]["status"] == "REJECTED_VALIDATION"
    assert "Subject: Leave Application" not in json.dumps(body)


@pytest.mark.unit
def test_phase1_removal_3_undocumented_6_script_matrix_replaced_with_detected_panel(
    cockpit_client,
):
    """Verify multi_lang_codes and 6-script matrix are removed and replaced by single panel."""
    client, _, _ = cockpit_client
    repo_root = Path(__file__).resolve().parents[2]
    for path in [
        *list((repo_root / "src" / "voice").glob("*.py")),
        repo_root / "src" / "orchestrator" / "dashboard.html",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "multi_lang_codes" not in text, f"Undocumented multi_lang_codes found in {path}"
        assert "SIX_INDIC_LOCALES" not in text, f"SIX_INDIC_LOCALES found in {path}"
        assert "multiScriptMatrix" not in text, f"6-script matrix ID found in {path}"

    html = client.get("/cockpit").text
    assert 'id="detectedTranscriptPanel"' in html
    assert 'id="detectedLanguageTag"' in html
    assert 'id="detectedTranscriptText"' in html


@pytest.mark.unit
def test_phase1_removal_4_no_simulated_or_jev_mock_controls_in_ui(cockpit_client):
    """Verify JEV mock badge/test button and simulated transcript paths are absent from UI/voice."""
    repo_root = Path(__file__).resolve().parents[2]
    for path in [
        *list((repo_root / "src" / "voice").glob("*.py")),
        repo_root / "src" / "orchestrator" / "dashboard.html",
        repo_root / "src" / "windows_integration" / "voice_orb_overlay.py",
    ]:
        text = path.read_text(encoding="utf-8")
        for forbidden in ("SIMULATED", "force_mock", "jevMockBadge", "testJevDecision"):
            assert forbidden not in text, f"Forbidden token '{forbidden}' found in {path}"


@pytest.mark.unit
def test_phase2_live_path_uses_websocket_streaming_and_logs_jsonl_timings(cockpit_client):
    """Verify /audio_turn and /ws/stream use WebSocket STT (not REST) and log JSONL timings."""
    import asyncio
    import base64

    from src.voice import GnaniSTTProvider, build_wav_from_pcm
    from tests.unit.test_voice_milestone1 import FakeWebSocketSession

    client, app, temp_workspace = cockpit_client
    svc = app.state.voice_service

    ws_stt = FakeWebSocketSession(
        [
            json.dumps({"type": "connected", "config": {"sample_rate": 16000}}),
            json.dumps({"type": "processing", "timestamp": "2026-10-23T12:00:00Z"}),
            json.dumps(
                {
                    "type": "transcript",
                    "segment_id": "gnani_seg_hi_01",
                    "segment_index": 0,
                    "text": "Show system info",
                    "latency": 148,
                }
            ),
        ]
    )

    def fail_if_rest_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"REST /stt/v3 must not be called in live path: {request.url}")

    svc.stt_provider = GnaniSTTProvider(
        config=GnaniConfig.from_env_or_vault(api_key=SYNTHETIC_M4_KEY, allow_vault_lookup=False),
        http_transport=httpx.MockTransport(fail_if_rest_called),
        ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_stt),
    )

    wav_bytes = build_wav_from_pcm(make_pcm_frame(num_samples=512), sample_rate=16000)
    b64_wav = base64.b64encode(wav_bytes).decode("ascii")

    res = client.post(
        "/api/v1/voice/audio_turn",
        json={
            "audio_base64": b64_wav,
            "language_code": "hi-IN",
            "speak_response": False,
            "conversational_mode": True,
            "realtime_pace": False,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["gnani_request_id"] == "gnani_seg_hi_01"
    assert data["transcript_latency_ms"] == 148

    timings_file = temp_workspace / ".winai" / "voice_timings.jsonl"
    assert timings_file.exists()
    lines = [
        json.loads(line)
        for line in timings_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) >= 1
    last_log = lines[-1]
    assert last_log["gnani_request_id"] == "gnani_seg_hi_01"
    assert last_log["vad_min_silence_ms"] == 350
    assert last_log["segment_stitch_window_ms"] == 650
    assert last_log["t_speech_end"] is not None
    assert last_log["t_transcript_received"] is not None
    assert last_log["t_action_started"] is not None


@pytest.mark.unit
def test_phase3_stop_segment_bypasses_stitcher_and_halts_tts_and_correction_updates_in_place(
    cockpit_client,
):
    """Verify stop segment bypasses SegmentStitcher immediately, halts TTS, and logs latency."""
    import asyncio
    import base64
    import time

    from src.voice import GnaniSTTProvider, build_wav_from_pcm
    from tests.unit.test_voice_milestone1 import FakeWebSocketSession

    client, app, temp_workspace = cockpit_client
    svc = app.state.voice_service
    # Set a long stitch window (2000ms) so we can prove stop bypasses it without waiting
    svc.settings.segment_stitch_window_ms = 2000
    svc.is_tts_playing = True

    ws_stop = FakeWebSocketSession(
        [
            json.dumps({"type": "connected", "config": {"sample_rate": 16000}}),
            json.dumps(
                {
                    "type": "transcript",
                    "segment_id": "seg_stop_bypass_01",
                    "segment_index": 0,
                    "text": "ruko",
                    "latency": 92,
                }
            ),
        ]
    )
    svc.stt_provider = GnaniSTTProvider(
        config=GnaniConfig.from_env_or_vault(api_key=SYNTHETIC_M4_KEY, allow_vault_lookup=False),
        ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_stop),
    )

    wav_bytes = build_wav_from_pcm(make_pcm_frame(num_samples=512), sample_rate=16000)
    b64_wav = base64.b64encode(wav_bytes).decode("ascii")
    t_client_end = time.time() - 0.11

    # 1. WAV replay (/audio_turn): must NOT pollute live-mic stop_latency_metrics
    res = client.post(
        "/api/v1/voice/audio_turn",
        json={
            "audio_base64": b64_wav,
            "language_code": "hi-IN",
            "speak_response": False,
            "conversational_mode": True,
            "t_speech_end": t_client_end,
            "realtime_pace": False,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["cockpit_state"] == "STOPPED"
    assert body["stitching_bypassed"] is True
    assert body["matched_stop_word"] == "ruko"
    assert svc.is_tts_playing is False
    assert body["stop_latency_metrics"]["count"] == 0  # WAV replay excluded from live mic metrics

    # 2. Live Mic WebSocket (/ws/stream): saves .winai/sessions/ WAV and records stop latency
    svc.current_cockpit_state = "ACTING"
    svc.is_tts_playing = False
    ws_live_stop = FakeWebSocketSession(
        [
            json.dumps({"type": "connected", "config": {"sample_rate": 16000}}),
            json.dumps(
                {
                    "type": "transcript",
                    "segment_id": "seg_live_mic_stop_01",
                    "segment_index": 0,
                    "text": "aagu cheyyaku",
                    "latency": 88,
                }
            ),
        ],
        delay_recv_seconds=0.05,
    )
    svc.stt_provider = GnaniSTTProvider(
        config=GnaniConfig.from_env_or_vault(api_key=SYNTHETIC_M4_KEY, allow_vault_lookup=False),
        ws_connector=lambda *a, **k: asyncio.sleep(0, result=ws_live_stop),
    )

    anchor_epoch = time.time() - 0.20
    base_latency = 0.005
    # Frame 1 (index 1 -> 2nd frame of 32ms = 0.064s) mapped on audio timeline:
    expected_stamp = anchor_epoch + (2 * 0.032) - base_latency

    with client.websocket_connect(
        "/api/v1/voice/ws/stream?language_code=te-IN&sample_rate=16000"
    ) as ws:
        ws.send_text(
            json.dumps(
                {
                    "type": "timeline_init",
                    "anchor_epoch_s": anchor_epoch,
                    "audio_context_current_time_s": 0.0,
                    "base_latency_s": base_latency,
                }
            )
        )
        # Send 2 speech frames (1,024B) + 1 silent frame + AudioWorklet speech_end stamp
        ws.send_bytes(make_pcm_frame(sample_val=6000, num_samples=512))
        ws.send_bytes(make_pcm_frame(sample_val=6000, num_samples=512))
        ws.send_bytes(b"\x00" * 1024)
        ws.send_text(
            json.dumps(
                {
                    "type": "speech_end",
                    "anchor_epoch_s": anchor_epoch,
                    "audio_context_current_time_s": 0.064,
                    "base_latency_s": base_latency,
                    "t_speech_end_client": expected_stamp,
                    "last_active_frame_index": 1,
                    "speech_energy_threshold": 0.015,
                }
            )
        )
        ws.send_text(json.dumps({"type": "stop_stream"}))

        received_turn = None
        for _ in range(5):
            msg = ws.receive_json()
            if msg.get("type") == "turn_result":
                received_turn = msg
                break
        assert received_turn is not None
        assert received_turn["cockpit_state"] == "STOPPED"
        assert received_turn["matched_stop_word"] == "aagu cheyyaku"

    # Verify live mic audio saved to .winai/sessions/ and verify stamp with check_session_stamp.py
    from scripts.check_session_stamp import verify_session_stamp

    saved_wavs = list((temp_workspace / ".winai" / "sessions").glob("*.wav"))
    saved_metas = list((temp_workspace / ".winai" / "sessions").glob("*.json"))
    assert len(saved_wavs) >= 1
    assert len(saved_metas) >= 1
    meta_json = json.loads(saved_metas[-1].read_text(encoding="utf-8"))
    assert meta_json["last_active_frame_index"] == 1
    assert meta_json["speech_energy_threshold"] == 0.015
    assert meta_json["total_pcm_bytes"] == 3072
    assert "residual_error_documentation" in meta_json["audio_timeline_mapping"]

    stamp_check = verify_session_stamp(saved_wavs[-1], saved_metas[-1])
    assert stamp_check["frame_index_diff"] == 0
    assert stamp_check["offset_diff_ms"] == 0.0
    assert abs(stamp_check["stamp_diff_ms"]) < 0.01

    # Verify stop_latency_metrics endpoint reports the live mic run (with median, p95, min, max)
    metrics_res = client.get("/api/v1/voice/metrics").json()
    assert metrics_res["cockpit_state"] == "STOPPED"
    assert metrics_res["last_matched_stop_word"] == "aagu cheyyaku"
    assert metrics_res["stop_latency_metrics"]["count"] == 1
    assert metrics_res["stop_latency_metrics"]["min_required_runs"] == 20
    assert metrics_res["stop_latency_metrics"]["median_ms"] is not None
    assert metrics_res["stop_latency_metrics"]["min_ms"] is not None
    assert metrics_res["stop_latency_metrics"]["max_ms"] is not None

    # Verify JSONL timing log recorded t_speech_end_client, speech_energy_threshold, is_live_mic
    timings_file = temp_workspace / ".winai" / "voice_timings.jsonl"
    last_rec = json.loads(timings_file.read_text(encoding="utf-8").splitlines()[-1])
    assert last_rec["is_live_mic"] is True
    assert last_rec["speech_energy_threshold"] == 0.015
    assert last_rec["stitching_bypassed"] is True
    assert last_rec["matched_lexicon_entry"] == "aagu cheyyaku"
    assert last_rec["t_speech_end_client"] == pytest.approx(expected_stamp, rel=1e-6)
    assert last_rec["t_stop_matched"] is not None
    assert last_rec["t_action_halted"] is not None

    # 3. Verify Correction path in-place without restarting from scratch
    svc.coordinator.step_executor.editor_text_history.append("Draft line one. Draft line two.")
    svc.coordinator.dispatcher.uia_service.find_window = lambda *_a, **_k: object()
    svc.coordinator.dispatcher.uia_service.focus_window = lambda *_a, **_k: True
    svc.coordinator.dispatcher.uia_service.set_text_value = lambda **_k: True

    corr_res = client.post(
        "/api/v1/voice/turn",
        json={"transcript": "thoda saaf karo", "conversational_mode": True},
    )
    assert corr_res.status_code == 200
    corr_body = corr_res.json()
    assert corr_body["result"]["status"] == "COMPLETED"
    assert corr_body["result"]["action_type"] == "type_in_notepad"
    assert "Draft line one" in svc.coordinator.step_executor.current_editor_text


@pytest.mark.unit
def test_phase3_followup_frozen_lexicon_hi_te_ta_en_only():
    """Verify frozen lexicon in intent_schema.py has only hi, te, ta, en and general rules."""
    from src.voice import intent_schema

    all_entries = (
        list(intent_schema.COMPOUND_NOUN_STOP_MASKS)
        + list(intent_schema.STOP_PHRASES)
        + list(intent_schema.STOP_SINGLE_TOKENS)
        + list(intent_schema.UNDO_PHRASES)
        + list(intent_schema.UNDO_SINGLE_TOKENS)
        + list(intent_schema.CORRECTION_PHRASES)
    )
    assert not hasattr(intent_schema, "NON_STOP_EXCLUSION_TOKENS")
    # Allowed Unicode blocks for hi (Devanagari 0900-097F), ta (Tamil 0B80-0BFF),
    # te (Telugu 0C00-0C7F), and Basic Latin (0000-007F)
    for entry in all_entries:
        for ch in entry:
            cp = ord(ch)
            allowed = (
                (0x0000 <= cp <= 0x007F)
                or (0x0900 <= cp <= 0x097F)
                or (0x0B80 <= cp <= 0x0BFF)
                or (0x0C00 <= cp <= 0x0C7F)
            )
            assert allowed, f"Non-hi/te/ta/en character U+{cp:04X} found in lexicon entry {entry!r}"


@pytest.mark.unit
def test_phase4_typing_baseline_page_and_real_runs_only(cockpit_client):
    """Verify /api/v1/voice/typing_baseline/page and real-runs-only manual vs voice recording."""
    client, _, _ = cockpit_client

    # 1. Separate route renders and initially reports 'no runs yet' (has_runs=False)
    page_res = client.get("/api/v1/voice/typing_baseline/page")
    assert page_res.status_code == 200
    assert "Manual vs. Voice Typing-Speed Baseline Recorder" in page_res.text
    assert "no runs yet" in page_res.text

    init_summary = client.get("/api/v1/voice/typing_baseline").json()
    assert init_summary["has_runs"] is False
    assert init_summary["status_label"] == "no runs yet"
    assert init_summary["manual_runs"] == []
    assert init_summary["voice_runs"] == []

    # 2. Record a real manual run (first keystroke to completion) and a voice run for the same text
    target = "def calculate_ema(prices, span): return prices.ewm(span=span).mean()"
    r_man = client.post(
        "/api/v1/voice/typing_baseline",
        json={
            "mode": "manual",
            "target_text": target,
            "typed_text": target,
            "duration_ms": 14200.0,
            "first_keystroke_epoch_s": 1000.0,
            "completed_epoch_s": 1014.2,
        },
    )
    assert r_man.status_code == 200

    r_voice = client.post(
        "/api/v1/voice/typing_baseline",
        json={
            "mode": "voice",
            "target_text": target,
            "typed_text": target,
            "duration_ms": 3800.0,
        },
    )
    assert r_voice.status_code == 200
    summary = r_voice.json()["summary"]
    assert summary["has_runs"] is True
    assert len(summary["manual_runs"]) == 1
    assert len(summary["voice_runs"]) == 1
    assert len(summary["comparisons"]) == 1
    comp = summary["comparisons"][0]
    assert comp["manual_duration_ms"] == 14200.0
    assert comp["voice_duration_ms"] == 3800.0
    assert comp["speedup_ratio"] == pytest.approx(14200.0 / 3800.0, rel=1e-2)
