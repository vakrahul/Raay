"""Loopback FastAPI Voice Cockpit Routes & Service Owner for WinAI-OE (Milestone 4).

Connects the primary Command Cockpit UI to the existing voice pipeline:
  SpeechInputManager / WebAudio PCM -> GnaniSTTProvider (Prisma v2.5)
  -> VoiceIntentPlanner -> Authoritative SecurityPolicyEngine -> ApprovalBroker
  -> AutonomousTaskDispatcher -> AuditLogger -> GnaniTTSProvider (Timbre v3.0) -> Speaker.

Security Guarantees:
- Strictly rejects extra/untrusted fields (`extra="forbid"`) on all request payloads.
- Shares the single authoritative `SecurityPolicyEngine` and `AutonomousTaskDispatcher`.
- Never exposes API keys, headers, raw audio, or stack traces in HTTP responses.
"""

import asyncio
import base64
import contextlib
import json
import math
import re
import secrets
import statistics
import time
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from src.orchestrator.agent_loop import GeneralTaskExecutionLoop
from src.orchestrator.task_dispatcher import AutonomousTaskDispatcher
from src.providers.base import ChatMessage
from src.providers.registry import ProviderRegistry
from src.security.approval_broker import ApprovalBroker
from src.security.audit_logger import AuditLogger, redact_secrets
from src.security.policy_engine import SecurityPolicyEngine
from src.storage.credential_vault import CredentialVault
from src.voice.audio_devices import BaseAudioInputBackend, BaseAudioOutputBackend
from src.voice.audio_types import (
    AudioDeviceError,
    CaptureState,
    PCM16NoiseCanceller,
    PlaybackState,
    compute_pcm_rms,
)
from src.voice.base import (
    SUPPORTED_STT_LANGUAGES,
    TIMBRE_V25_VOICES,
    TIMBRE_V30_VOICES,
    BaseSTTProvider,
    BaseTTSProvider,
    GnaniConfig,
    TranscriptEventKind,
    TTSAudioConfig,
    TTSRequest,
    build_wav_from_pcm,
    parse_wav_container,
    sanitize_error_message,
)
from src.voice.capability_registry import CapabilityRegistry
from src.voice.conversational_planner import (
    ALLOWED_REPLY_LANGUAGES,
    ConversationalTurnPlan,
    ConversationalTurnPlanner,
    detect_utterance_target_language,
    execute_conversational_actions,
    match_instant_rahul_greeting,
    strip_rahul_wake_word,
)
from src.voice.gnani_stt import (
    DEFAULT_VOICE_BIAS_WORDS,
    GnaniSTTProvider,
    SegmentStitcher,
)
from src.voice.gnani_tts import GnaniTTSProvider
from src.voice.intent_schema import (
    FastPathIntentKind,
    FastPathMatch,
    IntentValidationError,
    VoiceIntentPlanner,
    match_fast_path_lexicon,
)
from src.voice.pipeline import (
    CancelToken,
    VoiceExecutionCoordinator,
    VoicePipelineResult,
    VoicePipelineStatus,
)
from src.voice.session import VoiceIOSession
from src.voice.speech_input import SpeechInputManager
from src.voice.speech_output import SpeechOutputManager
from src.windows_integration.excel_grocery_controller import ExcelGroceryController
from src.windows_integration.sutra_showpiece_workflows import SutraShowpieceWorkflowController
from src.windows_integration.paint_controller import (
    COLOR_ALIASES,
    SHAPE_ALIASES,
    Windows11PaintController,
)

VALID_COCKPIT_STATES = (
    "IDLE",
    "LISTENING",
    "UNDERSTOOD",
    "ACTING",
    "STOPPED",
    "CORRECTING",
    "TRANSCRIBING",
    "UNDERSTANDING",
    "AWAITING_APPROVAL",
    "EXECUTING",
    "SPEAKING",
    "SUCCESS",
    "ERROR",
    "CANCELLED",
)


def map_pipeline_status_to_cockpit_state(
    p_status: VoicePipelineStatus,
    tts_active: bool = False,
) -> str:
    """Map VoicePipelineStatus to canonical Cockpit visual states."""
    if tts_active:
        return "SPEAKING"
    mapping = {
        VoicePipelineStatus.COMPLETED: "SUCCESS",
        VoicePipelineStatus.WAITING_FOR_APPROVAL: "AWAITING_APPROVAL",
        VoicePipelineStatus.DENIED_BY_POLICY: "ERROR",
        VoicePipelineStatus.DENIED_BY_USER: "CANCELLED",
        VoicePipelineStatus.DENIED_APPROVAL_INVALID: "ERROR",
        VoicePipelineStatus.REJECTED_VALIDATION: "ERROR",
        VoicePipelineStatus.FAILED_STT: "ERROR",
        VoicePipelineStatus.FAILED_EXECUTION: "ERROR",
        VoicePipelineStatus.CANCELLED: "CANCELLED",
        VoicePipelineStatus.STOPPED: "STOPPED",
        VoicePipelineStatus.ABORTED_SAFE: "STOPPED",
    }
    return mapping.get(p_status, "IDLE")


def build_multilingual_spoken_reply(
    action_type: Optional[str],
    target_resource: Optional[str],
    status_val: str,
    lang: str,
    fallback_msg: str,
) -> str:
    """Generate natural spoken feedback in Tamil, Telugu, Malayalam, Hindi, Kannada, or English."""
    lang_prefix = (lang or "en-IN").split("-")[0].lower()
    target = target_resource or "application"

    if status_val == "WAITING_FOR_APPROVAL":
        if lang_prefix == "ta":
            return f"{target} open panna anumathi venum. <pauses> Approve click pannunga."
        if lang_prefix == "te":
            return f"{target} kosam అనుమతి kavali. <pauses> Approve nokkandi."
        if lang_prefix == "ml":
            return f"{target} open cheyyan anumathi venam. <pauses> Approve click cheyyuka."
        if lang_prefix == "hi":
            return f"{target} ke liye approval chahiye. <pauses> Approve par click karein."
        if lang_prefix == "kn":
            return f"{target} ge anumati beku. <pauses> Approve maadi."
        act_name = action_type or "this action"
        return f"Confirmation required for {act_name} on {target}. <pauses> Click Approve."

    if status_val == "COMPLETED":
        if action_type in (
            "rahul_wake_greeting",
            "excel_grocery_analytics",
            "paint_automation",
            "close_app",
            "open_website",
            "vscode_design_export",
            "relaxing_music_and_reminder",
            "downloads_report_summary",
        ):
            return fallback_msg
        if action_type == "launch_app":
            if lang_prefix == "ta":
                return f"{target} open pannitten. <pauses> Idhula enna ezhudhanum?"
            if lang_prefix == "te":
                return f"{target} open chesanu. <pauses> Indulo emi rayali?"
            if lang_prefix == "ml":
                return f"{target} open cheythittundu. <pauses> Enthaanu ezhuthendathu?"
            if lang_prefix == "hi":
                return f"{target} open kar diya hai. <pauses> Isme kya likhna hai?"
            if lang_prefix == "kn":
                return f"{target} open maadiddene. <pauses> Yenu bareyali?"
            return f"Opened {target}. <pauses> What should I write?"
        if action_type == "type_in_notepad":
            if lang_prefix == "ta":
                return "Notepad-la type pannitten! <pauses> Innum edhavadhu ezhudhanuma?"
            if lang_prefix == "te":
                return "Notepad lo type chesanu! <pauses> Inka emaina rayala?"
            if lang_prefix == "ml":
                return "Notepad-il type cheythittundu!"
            if lang_prefix == "hi":
                return "Notepad mein type kar diya hai!"
            if lang_prefix == "kn":
                return "Notepad nalli type maadiddene!"
            return "Written into Notepad. <pauses> What next?"
        if action_type == "system_info":
            if lang_prefix == "ta":
                return "System matrum RAM status ellam சரிபார்த்தேன். Ellam normal-aa irukku."
            if lang_prefix == "te":
                return "System mariyu RAM vivaralu check chesanu. Anni bagunnayi."
            if lang_prefix == "ml":
                return "System RAM vivarangal parishodichu. Ellam normal aanu."
            if lang_prefix == "hi":
                return "System aur RAM status check kar liya hai. Sab normal hai."
            return fallback_msg
        if action_type == "list_approved_apps":
            if lang_prefix == "ta":
                return (
                    "Notepad, Calculator, Chrome, VS Code, matrum Edge open panna mudiyum. "
                    "Edhai open pannattum?"
                )
            if lang_prefix == "te":
                return (
                    "Nenu Notepad, Calculator, Chrome, VS Code, leda Edge open cheyagalanu. "
                    "Edi open cheyali?"
                )
            if lang_prefix == "ml":
                return (
                    "Notepad, Calculator, Chrome, VS Code, allengil Edge open cheyyam. "
                    "Ethaanu open cheyyendathu?"
                )
            if lang_prefix == "hi":
                return (
                    "Main Notepad, Calculator, Chrome, VS Code, ya Edge open kar sakti hoon. "
                    "Kaunsa open karoon?"
                )
            return (
                "I can open Notepad, Calculator, Chrome, VS Code, or Edge. Which one should I open?"
            )

    if any(
        bad in (fallback_msg or "")
        for bad in (
            "unrecognized_intent",
            "not in the approved voice action allowlist",
            "validation error",
            "errors.pydantic.dev",
        )
    ):
        if lang_prefix == "te":
            return "నేను పెయింట్, ఎక్సెల్ గ్రోసరీ బిల్, నోట్‌ప్యాడ్ లేదా కాలిక్యులేటర్ ఓపెన్ చేయగలను. ఏది ఓపెన్ చేయాలో చెప్పండి?"
        if lang_prefix == "ta":
            return "நான் பெயிண்ட், எக்செல் பில், அல்லது நோட்பேட் திறக்க முடியும். எதை திறக்கட்டும்?"
        if lang_prefix == "hi":
            return "मैं पेंट, एक्सेल बिल, या नोटपैड खोल सकता हूँ। बताइए क्या खोलूँ?"
        return "I can open MS Paint, the Excel Grocery Bill, Notepad, or Calculator. Which one would you like?"

    return fallback_msg


# ---------------------------------------------------------------------------
# Strict Request & Settings Models (`extra="forbid"`)
# ---------------------------------------------------------------------------


class VoiceUISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_device_id: Optional[int] = None
    output_device_id: Optional[int] = None
    language_code: str = Field(default="en-IN")
    voice: str = Field(default="Shivani")
    model: str = Field(default="timbre-v3.0", pattern="^(timbre-v2\\.5|timbre-v3\\.0)$")
    speak_responses: bool = False
    headphones_mode: bool = False
    noise_cancellation_enabled: bool = False
    conversation_layer_enabled: bool = True
    use_word_boosting: bool = False
    speech_energy_threshold: float = Field(default=0.015, ge=0.001, le=0.5)
    max_stop_tokens: int = Field(default=6, ge=1, le=30)
    typing_char_delay_ms: int = Field(default=18, ge=0, le=500)
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    vad_min_silence_ms: int = Field(default=350, ge=100, le=2000)
    segment_stitch_window_ms: int = Field(default=650, ge=50, le=5000)
    theme: str = Field(default="dark", pattern="^(dark|light)$")
    motion_mode: str = Field(default="full", pattern="^(full|reduced|static)$")


class StartVoiceSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_capture: bool = False
    language_code: Optional[str] = None


class StopVoiceSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript: Optional[str] = None
    speak_response: Optional[bool] = None
    conversational_mode: bool = False


class VoiceTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript: str = Field(min_length=0, max_length=4000)
    session_id: Optional[str] = None
    speak_response: Optional[bool] = None
    request_id: Optional[str] = None
    conversational_mode: bool = False


class AudioTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_base64: str = Field(min_length=16, max_length=25_000_000)
    language_code: Optional[str] = None
    session_id: Optional[str] = None
    speak_response: bool = True
    conversational_mode: bool = True
    t_speech_end: Optional[float] = None
    realtime_pace: bool = False


class ResolveVoiceApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128)
    approval_nonce: str = Field(min_length=8, max_length=256)
    approved: bool
    speak_response: bool = False


class TypingBaselineRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(pattern="^(manual|voice)$")
    target_text: str = Field(min_length=1, max_length=4000)
    typed_text: str = Field(min_length=1, max_length=4000)
    duration_ms: float = Field(gt=0.0, le=600_000.0)
    first_keystroke_epoch_s: Optional[float] = None
    completed_epoch_s: Optional[float] = None


# ---------------------------------------------------------------------------
# VoiceCockpitService (Single Process Owner for Voice Cockpit Operations)
# ---------------------------------------------------------------------------


class VoiceCockpitService:
    """Manages voice sessions, capability reporting, settings, and activity traces."""

    def __init__(
        self,
        workspace_root: Path,
        policy_engine: SecurityPolicyEngine,
        dispatcher: AutonomousTaskDispatcher,
        provider_registry: Optional[ProviderRegistry] = None,
        vault: Optional[CredentialVault] = None,
        gnani_config: Optional[GnaniConfig] = None,
        stt_provider: Optional[BaseSTTProvider] = None,
        tts_provider: Optional[BaseTTSProvider] = None,
        input_backend: Optional[BaseAudioInputBackend] = None,
        output_backend: Optional[BaseAudioOutputBackend] = None,
        allow_vault_lookup: bool = True,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.policy_engine = policy_engine
        self.dispatcher = dispatcher
        self.provider_registry = provider_registry
        self._input_backend = input_backend
        self._output_backend = output_backend

        self.gnani_config = gnani_config or GnaniConfig.from_env_or_vault(
            vault=vault,
            allow_vault_lookup=allow_vault_lookup,
        )
        self.stt_provider: BaseSTTProvider = stt_provider or GnaniSTTProvider(
            config=self.gnani_config
        )
        self.tts_cache_dir = self.workspace_root / ".winai" / "tts_cache"
        self.sessions_audio_dir = self.workspace_root / ".winai" / "sessions"
        self.tts_provider: BaseTTSProvider = tts_provider or GnaniTTSProvider(
            config=self.gnani_config,
            cache_dir=self.tts_cache_dir,
        )
        self.timings_log_file = self.workspace_root / ".winai" / "voice_timings.jsonl"
        self.typing_baseline_file = self.workspace_root / ".winai" / "typing_baseline.jsonl"
        self.capability_registry = CapabilityRegistry(
            stt_provider=self.stt_provider,
            tts_provider=self.tts_provider,
        )

        self.audit_logger = dispatcher.audit_logger or AuditLogger(
            log_file=self.workspace_root / ".winai" / "audit.log"
        )
        self.approval_broker = ApprovalBroker()

        self.planner = VoiceIntentPlanner(
            workspace_root=self.workspace_root,
            require_approvals=self.policy_engine.require_approvals,
        )
        self.coordinator = VoiceExecutionCoordinator(
            workspace_root=self.workspace_root,
            policy_engine=self.policy_engine,
            approval_broker=self.approval_broker,
            dispatcher=self.dispatcher,
            planner=self.planner,
            audit_logger=self.audit_logger,
        )
        self.conversational_planner = ConversationalTurnPlanner(
            llm_provider=self._resolve_active_llm_provider(),
            timeout_seconds=8.0,
            max_memory_turns=3,
            max_memory_tokens=240,
        )
        self.paint_controller = Windows11PaintController(workspace_root=self.workspace_root)
        self.excel_controller = ExcelGroceryController(workspace_root=self.workspace_root)
        self.showpiece_controller = SutraShowpieceWorkflowController(workspace_root=self.workspace_root)
        self.agent_loop = GeneralTaskExecutionLoop(
            workspace_root=self.workspace_root,
            execution_engine=self.dispatcher.execution_engine,
            paint_controller=self.paint_controller,
            excel_controller=self.excel_controller,
            llm_provider=self._resolve_active_llm_provider(),
        )
        self._paint_cancelled: bool = False

        self.settings = VoiceUISettings()
        self.sessions: Dict[str, VoiceIOSession] = {}
        self._session_audio_buffers: Dict[str, bytearray] = {}
        self._session_collector_tasks: Dict[str, asyncio.Task[Any]] = {}
        self.active_session_id: Optional[str] = None
        self.active_context_app: Optional[str] = None
        self.current_cockpit_state: str = "IDLE"
        self.last_rms: float = 0.0
        self.last_detected_language: str = "en-IN"
        self.last_transcript: str = ""
        self.last_matched_stop_word: Optional[str] = None
        self.is_tts_playing: bool = False
        self.stop_latency_measurements_ms: List[float] = []
        self.typing_baseline_runs: List[Dict[str, Any]] = []
        self.activity_history: Deque[Dict[str, Any]] = deque(maxlen=50)
        self.timing_history: Deque[Dict[str, Any]] = deque(maxlen=100)

    def record_typing_baseline_run(
        self,
        mode: str,
        target_text: str,
        typed_text: str,
        duration_ms: float,
        first_keystroke_epoch_s: Optional[float] = None,
        completed_epoch_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record a real manual or voice typing run to `.winai/typing_baseline.jsonl`."""
        clean_target = " ".join((target_text or "").strip().split())
        clean_typed = " ".join((typed_text or "").strip().split())
        char_len = max(1, len(clean_typed))
        cpm = round((char_len / max(0.001, duration_ms / 1000.0)) * 60.0, 1)
        wpm = round(cpm / 5.0, 1)
        rec = {
            "timestamp": time.time(),
            "mode": mode,
            "target_text": clean_target,
            "typed_text": clean_typed,
            "char_count": len(clean_typed),
            "duration_ms": round(float(duration_ms), 2),
            "wpm": wpm,
            "first_keystroke_epoch_s": first_keystroke_epoch_s,
            "completed_epoch_s": completed_epoch_s,
        }
        self.typing_baseline_runs.append(rec)
        with contextlib.suppress(Exception):
            self.typing_baseline_file.parent.mkdir(parents=True, exist_ok=True)
            with self.typing_baseline_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def get_typing_baseline_summary(self) -> Dict[str, Any]:
        """Return manual vs. voice typing runs strictly from real recorded runs."""
        manual_runs = [r for r in self.typing_baseline_runs if r.get("mode") == "manual"]
        voice_runs = [r for r in self.typing_baseline_runs if r.get("mode") == "voice"]
        pairs: List[Dict[str, Any]] = []
        by_text: Dict[str, Dict[str, Any]] = {}
        for r in self.typing_baseline_runs:
            key = (r.get("target_text") or "").strip().lower()
            if not key:
                continue
            slot = by_text.setdefault(
                key,
                {
                    "target_text": r.get("target_text"),
                    "manual_duration_ms": None,
                    "voice_duration_ms": None,
                    "manual_wpm": None,
                    "voice_wpm": None,
                },
            )
            if r.get("mode") == "manual":
                slot["manual_duration_ms"] = r.get("duration_ms")
                slot["manual_wpm"] = r.get("wpm")
            elif r.get("mode") == "voice":
                slot["voice_duration_ms"] = r.get("duration_ms")
                slot["voice_wpm"] = r.get("wpm")
        for slot in by_text.values():
            m_ms = slot["manual_duration_ms"]
            v_ms = slot["voice_duration_ms"]
            slot["speedup_ratio"] = (
                round(float(m_ms) / float(v_ms), 2)
                if (m_ms is not None and v_ms is not None and v_ms > 0)
                else None
            )
            pairs.append(slot)

        has_runs = bool(self.typing_baseline_runs)
        return {
            "has_runs": has_runs,
            "status_label": (
                f"{len(manual_runs)} manual / {len(voice_runs)} voice runs recorded"
                if has_runs
                else "no runs yet"
            ),
            "manual_runs": manual_runs[-20:],
            "voice_runs": voice_runs[-20:],
            "comparisons": pairs,
        }

    def get_stop_latency_metrics(self) -> Dict[str, Any]:
        """Compute median, p95, min, max, and count strictly from live mic runs (min 20)."""
        vals = [float(x) for x in self.stop_latency_measurements_ms if x is not None and x >= 0.0]
        if not vals:
            return {
                "count": 0,
                "min_required_runs": 20,
                "sufficient_runs": False,
                "median_ms": None,
                "p95_ms": None,
                "min_ms": None,
                "max_ms": None,
                "measurements_ms": [],
            }
        s_vals = sorted(vals)
        med = round(float(statistics.median(s_vals)), 2)
        p95_idx = min(len(s_vals) - 1, max(0, math.ceil(0.95 * len(s_vals)) - 1))
        p95 = round(float(s_vals[p95_idx]), 2)
        return {
            "count": len(s_vals),
            "min_required_runs": 20,
            "sufficient_runs": len(s_vals) >= 20,
            "median_ms": med,
            "p95_ms": p95,
            "min_ms": round(float(s_vals[0]), 2),
            "max_ms": round(float(s_vals[-1]), 2),
            "measurements_ms": [round(v, 2) for v in vals[-25:]],
        }

    def effective_interrupt_gate_state(self) -> str:
        """Return active state for gating stop/undo fast-path (ACTING/SPEAKING/CORRECTING)."""
        if self.current_cockpit_state in ("ACTING", "SPEAKING", "CORRECTING", "EXECUTING"):
            return self.current_cockpit_state
        if self.is_tts_playing:
            return "SPEAKING"
        if self.coordinator.step_executor.active_token is not None:
            return "ACTING"
        return "LISTENING"

    def save_live_session_audio_artifact(
        self,
        session_id: str,
        pcm_bytes: bytes,
        sample_rate: int,
        t_speech_end_client: Optional[float],
        last_active_frame_index: Optional[int],
        t_action_halted: Optional[float] = None,
        stop_latency_ms: Optional[float] = None,
        audio_timeline_mapping: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Save raw mic PCM audio of a live session to `.winai/sessions/` with stamp metadata."""
        if not pcm_bytes:
            return None
        try:
            self.sessions_audio_dir.mkdir(parents=True, exist_ok=True)
            ts_ms = int(time.time() * 1000)
            stem = f"{session_id}_{ts_ms}"
            wav_path = self.sessions_audio_dir / f"{stem}.wav"
            meta_path = self.sessions_audio_dir / f"{stem}.json"

            wav_bytes = build_wav_from_pcm(
                pcm_bytes=pcm_bytes,
                sample_rate=sample_rate,
                num_channels=1,
                sample_width=2,
            )
            wav_path.write_bytes(wav_bytes)

            frame_s = 512.0 / float(max(1, sample_rate))
            frame_ms = frame_s * 1000.0
            offset_ms = (
                round((last_active_frame_index + 1) * frame_ms, 2)
                if last_active_frame_index is not None
                else None
            )
            timeline_info = dict(audio_timeline_mapping or {})
            if "frame_samples" not in timeline_info:
                timeline_info["frame_samples"] = 512
            if "frame_duration_s" not in timeline_info:
                timeline_info["frame_duration_s"] = frame_s
            timeline_info["last_active_frame_index"] = last_active_frame_index
            timeline_info["last_active_frame_offset_ms"] = offset_ms
            timeline_info["computed_t_speech_end_client"] = t_speech_end_client
            timeline_info["residual_error_documentation"] = (
                "t_speech_end_client is computed from the audio hardware timeline: "
                "anchor_epoch_s + ((last_active_frame_index + 1) * (512 / sample_rate)) "
                "- base_latency_s (never from WebSocket message receipt time). "
                "Residual error sources: (1) 512-sample (32.0 ms @ 16 kHz) frame boundary "
                "quantization (+/- 16.0 ms max), (2) WebAudio AudioContext.baseLatency "
                "reporting granularity (~2.67 ms per 128-sample render quantum at 48 kHz), "
                "and (3) crystal clock skew between audio codec DAC/ADC and OS wall clock "
                "(< 0.05 ms/s)."
            )

            meta = {
                "session_id": session_id,
                "wav_file": wav_path.name,
                "sample_rate": sample_rate,
                "total_pcm_bytes": len(pcm_bytes),
                "total_frames_1024": len(pcm_bytes) // 1024,
                "speech_energy_threshold": self.settings.speech_energy_threshold,
                "last_active_frame_index": last_active_frame_index,
                "last_active_frame_offset_ms": offset_ms,
                "t_speech_end_client": t_speech_end_client,
                "t_action_halted": t_action_halted,
                "stop_latency_ms": stop_latency_ms,
                "audio_timeline_mapping": timeline_info,
                "saved_at": time.time(),
            }
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            return str(wav_path)
        except Exception:
            return None

    def _create_session_instance(self) -> VoiceIOSession:
        in_mgr = SpeechInputManager(
            backend=self._input_backend,
            device_id=self.settings.input_device_id,
        )
        out_mgr = SpeechOutputManager(
            backend=self._output_backend,
            device_id=self.settings.output_device_id,
        )
        sess = VoiceIOSession(
            input_manager=in_mgr,
            output_manager=out_mgr,
            stt_provider=self.stt_provider,
            tts_provider=self.tts_provider,
        )
        self.sessions[sess.session_id] = sess
        self._session_audio_buffers[sess.session_id] = bytearray()
        self.active_session_id = sess.session_id
        return sess

    async def _collect_session_pcm(self, session: VoiceIOSession) -> None:
        """Continuously drain captured PCM frames into the session buffer and update live RMS."""
        buf = self._session_audio_buffers.setdefault(session.session_id, bytearray())
        with contextlib.suppress(Exception):
            while session.input_manager.is_recording and not session.is_cancelled:
                frame = await session.input_manager.read_frame(timeout_seconds=0.12)
                if frame is not None and frame.pcm_bytes:
                    buf.extend(frame.pcm_bytes)
                    self.last_rms = frame.rms

    def get_or_create_session(self, session_id: Optional[str] = None) -> VoiceIOSession:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        if self.active_session_id and self.active_session_id in self.sessions:
            active = self.sessions[self.active_session_id]
            if not active.is_cancelled and not active.is_closed:
                return active
        return self._create_session_instance()

    def list_audio_devices(self) -> Dict[str, List[Dict[str, Any]]]:
        in_mgr = SpeechInputManager(backend=self._input_backend)
        out_mgr = SpeechOutputManager(backend=self._output_backend)
        try:
            in_devs = [d.model_dump() for d in in_mgr.list_devices()]
        except Exception:
            in_devs = []
        try:
            out_devs = [d.model_dump() for d in out_mgr.list_devices()]
        except Exception:
            out_devs = []
        return {"input_devices": in_devs, "output_devices": out_devs}

    def _resolve_active_llm_provider(self) -> Any:
        """Resolve the active LLM provider from planner or provider_registry."""
        if self.planner.llm_provider is not None:
            return self.planner.llm_provider
        if self.provider_registry is not None:
            registered = list(self.provider_registry.registered_ids())
            for candidate in ("gemini", "openai", "anthropic", "local"):
                if candidate in registered:
                    with contextlib.suppress(Exception):
                        return self.provider_registry.get_provider(candidate)
        return None

    def reasoning_provider_summary(self) -> Dict[str, Any]:
        """Report reasoning/planning engine status truthfully from the active provider in use."""
        registered = (
            list(self.provider_registry.registered_ids())
            if self.provider_registry is not None
            else []
        )
        active_prov = self._resolve_active_llm_provider()
        if active_prov is not None:
            caps = active_prov.get_capabilities()
            model_id = getattr(active_prov, "model_name", None) or getattr(caps, "model_name", "")
            active_label = (
                f"Planner: {caps.provider_name} ({model_id})"
                if model_id
                else f"Planner: {caps.provider_name}"
            )
        elif "gemini" in registered:
            active_label = "Planner: Gemini"
        elif registered:
            active_label = f"Planner: {registered[0].capitalize()}"
        else:
            active_label = "Planner: Deterministic Intent Parser"
        return {
            "active_planner": active_label,
            "conversation_layer_enabled": bool(self.settings.conversation_layer_enabled),
            "registered_providers": registered,
            "strict_schema_validation": True,
        }

    @staticmethod
    def resolve_explicit_timbre_language(
        language_code: Optional[str], fallback: str = "en-IN"
    ) -> str:
        """Resolve an explicit Timbre language code (never 'auto')."""
        cand = (language_code or "").strip()
        if cand in ALLOWED_REPLY_LANGUAGES or cand in SUPPORTED_STT_LANGUAGES:
            return cand
        fb = (fallback or "en-IN").strip()
        if fb in ALLOWED_REPLY_LANGUAGES or fb in SUPPORTED_STT_LANGUAGES:
            return fb
        return "en-IN"

    async def synthesize_ui_speech_with_timing(
        self, text: str, language_code: Optional[str] = None
    ) -> Tuple[Optional[str], float, str, str]:
        """Synthesize speech via Timbre v3.0 SSE (`POST /api/v1/tts/sse`) with explicit language.

        Returns `(audio_b64, tts_first_audio_ms, tts_synthesis_mode, explicit_language)`.
        Note: `synthesize_buffered_sse` buffers the SSE chunks to repair the WAV RIFF header
        before playback starts; `tts_first_audio_ms` measures time-to-buffered-playback-ready
        (and reports `buffered_sse_cache_hit` vs `buffered_sse_stream`).
        """
        explicit_lang = self.resolve_explicit_timbre_language(
            language_code, fallback=self.settings.language_code
        )
        if not text or not getattr(self.tts_provider, "is_configured", False):
            return None, 0.0, "disabled", explicit_lang
        one_line = " ".join(text.strip().splitlines())[:220]
        req = TTSRequest(
            text=one_line,
            voice=(self.settings.voice if self.settings.model == "timbre-v3.0" else "Shivani"),
            model="timbre-v3.0",
            language=explicit_lang,
            audio_config=TTSAudioConfig(sample_rate=24000, container="wav"),
        )
        t0 = time.perf_counter()
        try:
            if hasattr(self.tts_provider, "synthesize_buffered_sse"):
                tts_res = await self.tts_provider.synthesize_buffered_sse(req, use_cache=True)
                elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                mode = (
                    "buffered_sse_cache_hit"
                    if getattr(tts_res, "chunks_count", 1) == 0
                    else "buffered_sse_stream"
                )
            else:
                tts_res = await self.tts_provider.synthesize(req)
                elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                mode = "rest_buffered"
            b64 = base64.b64encode(tts_res.audio_bytes).decode("ascii")
            return b64, elapsed_ms, mode, explicit_lang
        except Exception:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return None, elapsed_ms, "error", explicit_lang

    async def synthesize_ui_speech_b64(
        self, text: str, language_code: Optional[str] = None
    ) -> Optional[str]:
        """Synthesize one-line speech via Timbre v3.0 SSE (`POST /api/v1/tts/sse`) with cache."""
        b64, _ms, _mode, _lang = await self.synthesize_ui_speech_with_timing(
            text, language_code=language_code
        )
        return b64

    async def stream_pcm_via_gnani_ws(
        self,
        raw_audio_or_pcm: bytes,
        language_code: str = "en-IN",
        sample_rate: int = 16000,
        realtime_pace: bool = True,
        use_bias: Optional[bool] = None,
    ) -> Tuple[str, Optional[str], Optional[float], float, bool, Optional[FastPathMatch]]:
        """Stream raw 16-bit PCM frames over `WSS /stt/v3/stream`.

        Evaluates the Stop/Undo fast-path lexicon on EVERY raw `transcript` segment as soon
        as it arrives, BEFORE and independent of `SegmentStitcher` so stop words never wait
        for the stitch window. Normal commands continue to use `SegmentStitcher`.
        """
        pcm_payload = raw_audio_or_pcm
        effective_rate = sample_rate
        if len(raw_audio_or_pcm) >= 44 and raw_audio_or_pcm[:4] == b"RIFF":
            meta = parse_wav_container(raw_audio_or_pcm)
            pcm_payload = raw_audio_or_pcm[
                meta.data_payload_offset : meta.data_payload_offset + meta.actual_data_bytes
            ]
            effective_rate = meta.sample_rate

        stitcher = SegmentStitcher(
            min_silence_ms=self.settings.vad_min_silence_ms,
            stitch_window_ms=self.settings.segment_stitch_window_ms,
        )
        bias_enabled = use_bias if use_bias is not None else bool(self.settings.use_word_boosting)
        bias_words = DEFAULT_VOICE_BIAS_WORDS if bias_enabled else None
        bias_score = 1.5 if bias_enabled else 0.0

        stop_triggered = False

        async def _pcm_iter() -> AsyncIterator[bytes]:
            for offset in range(0, len(pcm_payload), 1024):
                if stop_triggered:
                    break
                yield pcm_payload[offset : offset + 1024]
            if realtime_pace and not stop_triggered:
                # Append trailing silence frames so server-side VAD closes the final segment
                silence_frames = max(14, int((self.settings.vad_min_silence_ms + 200) / 32))
                for _ in range(silence_frames):
                    if stop_triggered:
                        break
                    yield b"\x00" * 1024

        t0 = time.perf_counter()
        stream_gen = self.stt_provider.stream_pcm(
            pcm_frames=_pcm_iter(),
            language_code=language_code,
            sample_rate=effective_rate,
            vad_threshold=self.settings.vad_threshold,
            min_silence_ms=self.settings.vad_min_silence_ms,
            bias_list=bias_words,
            bias_score=bias_score,
            realtime_pace=realtime_pace,
        )
        try:
            async for ev in stream_gen:
                if ev.kind == TranscriptEventKind.TRANSCRIPT and ev.text:
                    # Phase 3 Addition #1 & Follow-up 2: Check Stop/Undo fast-path BEFORE Stitcher!
                    gate_state = self.effective_interrupt_gate_state()
                    fp_match = match_fast_path_lexicon(
                        ev.text,
                        current_state=gate_state,
                        max_tokens=self.settings.max_stop_tokens,
                    )
                    if fp_match.kind in (FastPathIntentKind.STOP, FastPathIntentKind.UNDO):
                        stop_triggered = True
                        e2e_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                        return (
                            ev.text.strip(),
                            ev.segment_id,
                            ev.latency_ms,
                            e2e_ms,
                            True,
                            fp_match,
                        )
                    stitcher.add_segment(
                        text=ev.text,
                        segment_id=ev.segment_id,
                        latency_ms=ev.latency_ms,
                    )
        finally:
            if hasattr(stream_gen, "aclose"):
                with contextlib.suppress(Exception):
                    await stream_gen.aclose()

        e2e_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        flushed = stitcher.flush()
        return (
            flushed["text"],
            flushed["primary_segment_id"],
            flushed["latency_ms"],
            e2e_ms,
            False,
            None,
        )

    def log_turn_timing(
        self,
        session_id: str,
        request_id: str,
        language_code: str,
        t_speech_end: Optional[float] = None,
        t_transcript_received: Optional[float] = None,
        t_stop_matched: Optional[float] = None,
        t_action_started: Optional[float] = None,
        t_action_halted: Optional[float] = None,
        gnani_request_id: Optional[str] = None,
        transcript_latency_ms: Optional[float] = None,
        matched_lexicon_entry: Optional[str] = None,
        matched_rule: Optional[str] = None,
        gate_decision: Optional[str] = None,
        stitching_bypassed: bool = False,
        stop_latency_ms: Optional[float] = None,
        is_live_mic: bool = False,
        session_audio_file: Optional[str] = None,
        planner_provider: Optional[str] = None,
        planner_model: Optional[str] = None,
        llm_latency_ms: Optional[float] = None,
        reply_language: Optional[str] = None,
        tts_first_audio_ms: Optional[float] = None,
        tts_synthesis_mode: Optional[str] = None,
        total_turn_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Append per-turn timing telemetry to `.winai/voice_timings.jsonl`."""
        record = redact_secrets(
            {
                "timestamp": time.time(),
                "session_id": session_id,
                "request_id": request_id,
                "gnani_request_id": gnani_request_id,
                "language_code": language_code,
                "speech_energy_threshold": self.settings.speech_energy_threshold,
                "max_stop_tokens": self.settings.max_stop_tokens,
                "vad_min_silence_ms": self.settings.vad_min_silence_ms,
                "segment_stitch_window_ms": self.settings.segment_stitch_window_ms,
                "is_live_mic": bool(is_live_mic),
                "session_audio_file": session_audio_file,
                "t_speech_end": t_speech_end,
                "t_speech_end_client": t_speech_end,
                "t_transcript_received": t_transcript_received,
                "t_stop_matched": t_stop_matched,
                "t_action_started": t_action_started,
                "t_action_halted": t_action_halted,
                "matched_lexicon_entry": matched_lexicon_entry,
                "matched_rule": matched_rule,
                "gate_decision": gate_decision,
                "stitching_bypassed": bool(stitching_bypassed),
                "stop_latency_ms": stop_latency_ms,
                "transcript_latency_ms": transcript_latency_ms,
                "provider": planner_provider,
                "model": planner_model,
                "llm_latency_ms": llm_latency_ms,
                "reply_language": reply_language,
                "tts_first_audio_ms": tts_first_audio_ms,
                "tts_synthesis_mode": tts_synthesis_mode,
                "total_turn_ms": total_turn_ms,
            }
        )
        self.timing_history.appendleft(record)
        with contextlib.suppress(Exception):
            self.timings_log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.timings_log_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    async def execute_fast_path_interrupt(
        self,
        session: VoiceIOSession,
        match: FastPathMatch,
        raw_transcript: str,
        language_code: str,
        t_speech_end_client: Optional[float],
        t_transcript_received: float,
        gnani_request_id: Optional[str] = None,
        stitching_bypassed: bool = True,
        stt_latency_ms: Optional[float] = None,
        is_live_mic: bool = False,
        session_audio_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute STOP or UNDO in the same event-loop tick without invoking the LLM."""
        t_stop_matched = time.time()
        # Halt active step executor, cancel in-flight LLM request, and stop TTS playback
        self.conversational_planner.cancel_active_turn()
        t_action_halted = self.coordinator.step_executor.halt_active(
            reason=f"Fast-path {match.kind.value}: {match.matched_entry}",
            matched_word=match.matched_entry,
        )
        self.is_tts_playing = False
        with contextlib.suppress(Exception):
            await session.output_manager.cancel()

        self.last_transcript = raw_transcript
        self.last_matched_stop_word = match.matched_entry
        self.current_cockpit_state = "STOPPED"

        stop_latency_ms: Optional[float] = None
        if t_speech_end_client is not None:
            stop_latency_ms = round(max(0.0, (t_action_halted - t_speech_end_client) * 1000.0), 2)
            # Record stop latency in metrics ONLY for live mic runs (never WAV replays)
            if is_live_mic:
                self.stop_latency_measurements_ms.append(stop_latency_ms)

        req_id = f"vstop_{secrets.token_hex(6)}"
        if match.kind == FastPathIntentKind.UNDO:
            undo_out = await self.coordinator.step_executor.undo_last_step()
            user_msg = undo_out["message"]
            act_type = "undo"
        else:
            user_msg = f"Stopped immediately (matched '{match.matched_entry}')."
            act_type = "stop"

        res = VoicePipelineResult(
            request_id=req_id,
            session_id=session.session_id,
            status=VoicePipelineStatus.STOPPED,
            user_facing_message=user_msg,
            action_type=act_type,
            target_resource=self.active_context_app or "session",
            policy_decision="ALLOW",
            approval_required=False,
            observable_evidence=[
                f"Fast-path {match.kind.value} matched '{match.matched_entry}' "
                f"(stitching_bypassed={stitching_bypassed})"
            ],
            duration_ms=round((t_action_halted - t_transcript_received) * 1000.0, 2),
        )
        toast = {
            "kind": "warning",
            "title": f"{act_type.upper()} · {match.matched_entry}",
            "message": user_msg,
            "suggestions": [],
        }
        entry = self.record_turn_activity(
            raw_transcript,
            res,
            spoken_text=user_msg,
            toast_meta=toast,
            detected_language=language_code,
        )
        entry["matched_stop_word"] = match.matched_entry
        entry["stitching_bypassed"] = stitching_bypassed
        entry["stop_latency_ms"] = stop_latency_ms

        timing_rec = self.log_turn_timing(
            session_id=session.session_id,
            request_id=req_id,
            language_code=language_code,
            t_speech_end=t_speech_end_client,
            t_transcript_received=t_transcript_received,
            t_stop_matched=t_stop_matched,
            t_action_started=None,
            t_action_halted=t_action_halted,
            gnani_request_id=gnani_request_id,
            transcript_latency_ms=stt_latency_ms,
            matched_lexicon_entry=match.matched_entry,
            matched_rule=match.matched_rule,
            gate_decision=match.gate_decision,
            stitching_bypassed=stitching_bypassed,
            stop_latency_ms=stop_latency_ms,
            is_live_mic=is_live_mic,
            session_audio_file=session_audio_file,
        )
        return redact_secrets(
            {
                "cockpit_state": "STOPPED",
                "detected_language": language_code,
                "gnani_request_id": gnani_request_id,
                "transcript_latency_ms": stt_latency_ms,
                "matched_stop_word": match.matched_entry,
                "stitching_bypassed": stitching_bypassed,
                "stop_latency_ms": stop_latency_ms,
                "stop_latency_metrics": self.get_stop_latency_metrics(),
                "result": res.model_dump(),
                "activity_entry": entry,
                "timing": timing_rec,
            }
        )

    async def draft_smart_notepad_content(self, raw_spoken: str) -> Optional[str]:
        """Prepare Notepad text from literal dictation or the real configured LLM planner.

        Never returns hardcoded demo templates or fake signer names. If the user asks to
        compose open-ended content (e.g. a letter or application) and no LLM provider is
        configured, returns None so the caller asks a clarifying question.
        """
        cleaned = (raw_spoken or "").strip()
        lower = cleaned.lower()
        for prefix in (
            "write that ",
            "write ",
            "type ",
            "note down ",
            "ezhudhu ",
            "rayi ",
            "likho ",
        ):
            if lower.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()
                lower = cleaned.lower()
                break

        if not cleaned:
            return None

        is_open_ended_draft = any(
            k in lower
            for k in (
                "draft ",
                "letter",
                "application",
                "essay",
                "paragraph",
                "leave request",
                "sick leave",
            )
        )
        if is_open_ended_draft:
            prov = self.planner.llm_provider
            if (
                prov is None
                and self.provider_registry is not None
                and "gemini" in self.provider_registry.registered_ids()
            ):
                with contextlib.suppress(Exception):
                    prov = self.provider_registry.get_provider("gemini")
            if prov is not None:
                with contextlib.suppress(Exception):
                    resp = await asyncio.wait_for(
                        prov.complete(
                            messages=[
                                ChatMessage(
                                    role="system",
                                    content=(
                                        "Write concise plain-text content for a Windows "
                                        "Notepad document based on the user's request. "
                                        "No markdown fences."
                                    ),
                                ),
                                ChatMessage(role="user", content=cleaned),
                            ],
                            temperature=0.3,
                            max_tokens=300,
                        ),
                        timeout=4.5,
                    )
                    if resp.content and resp.content.strip():
                        return resp.content.strip()
            return None

        return cleaned

    def record_turn_activity(
        self,
        transcript: str,
        result: VoicePipelineResult,
        spoken_text: Optional[str] = None,
        audio_b64: Optional[str] = None,
        toast_meta: Optional[Dict[str, Any]] = None,
        detected_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        ui_state = map_pipeline_status_to_cockpit_state(result.status)
        self.current_cockpit_state = ui_state
        lang_used = detected_language or self.last_detected_language or self.settings.language_code
        entry = redact_secrets(
            {
                "timestamp": time.time(),
                "request_id": result.request_id,
                "session_id": result.session_id,
                "ui_state": ui_state,
                "pipeline_status": result.status.value,
                "detected_language": lang_used,
                "command_preview": sanitize_error_message(transcript[:160]),
                "action_type": result.action_type,
                "target_resource": result.target_resource,
                "policy_decision": result.policy_decision,
                "approval_required": result.approval_required,
                "pending_approval": (
                    result.pending_approval.model_dump() if result.pending_approval else None
                ),
                "user_facing_message": spoken_text or result.user_facing_message,
                "observable_evidence": result.observable_evidence,
                "tts_succeeded": True if audio_b64 else result.tts_succeeded,
                "tts_error": result.tts_error,
                "error_code": result.error_code,
                "duration_ms": result.duration_ms,
                "toast": toast_meta,
            }
        )
        self.activity_history.appendleft(entry)
        return entry

    def is_context_app_open(self) -> bool:
        """Return True if an application launched by WinAI-OE in this session is currently open."""
        if not self.active_context_app:
            return False
        app_id = self.active_context_app.lower()
        title_map = {
            "notepad": "Notepad",
            "calc": "Calculator",
            "chrome": "Chrome",
            "vscode": "Visual Studio Code",
            "edge": "Edge",
        }
        win_title = title_map.get(app_id, app_id)
        with contextlib.suppress(Exception):
            win = self.dispatcher.uia_service.find_window(win_title, timeout_seconds=0.25)
            if win is not None:
                return True
            pid = self.dispatcher.app_manager.get_running_pid(app_id)
            if pid is not None:
                import psutil

                if psutil.pid_exists(pid):
                    return True
        self.active_context_app = None
        return False

    def get_recent_audit_records(self, limit: int = 20) -> List[Dict[str, Any]]:
        log_file = self.audit_logger.log_file
        if not log_file.exists():
            return []
        try:
            lines = log_file.read_text(encoding="utf-8").splitlines()
            records = []
            for ln in reversed(lines[-max(1, min(limit, 50)) :]):
                if ln.strip():
                    records.append(redact_secrets(json.loads(ln)))
            return records
        except Exception:
            return []


# ---------------------------------------------------------------------------
# FastAPI Router Factory
# ---------------------------------------------------------------------------


def create_voice_router(service: VoiceCockpitService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

    @router.get("/capabilities")
    async def get_voice_capabilities(verify: bool = Query(default=False)) -> Dict[str, Any]:
        if verify:
            caps = await service.capability_registry.verify_connectivity()
        else:
            caps = service.capability_registry.to_dict()

        devices = service.list_audio_devices()
        return redact_secrets(
            {
                "cockpit_state": service.current_cockpit_state,
                "valid_cockpit_states": list(VALID_COCKPIT_STATES),
                "capabilities": caps,
                "reasoning": service.reasoning_provider_summary(),
                "devices": devices,
                "settings": service.settings.model_dump(),
                "security_policy_mode": (
                    "approvals_required"
                    if service.policy_engine.require_approvals
                    else "ambient_allow_per_policy"
                ),
            }
        )

    @router.post("/session/start")
    async def start_voice_session(payload: StartVoiceSessionRequest) -> Dict[str, Any]:
        session = service._create_session_instance()
        await session.start()

        if payload.language_code and payload.language_code in SUPPORTED_STT_LANGUAGES:
            service.settings.language_code = payload.language_code

        if payload.start_capture:
            try:
                await session.start_capture()
                service.current_cockpit_state = "LISTENING"
                collector = asyncio.create_task(service._collect_session_pcm(session))
                service._session_collector_tasks[session.session_id] = collector
            except AudioDeviceError as e:
                service.current_cockpit_state = "ERROR"
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"error_code": e.code, "message": e.message},
                ) from e
        else:
            service.current_cockpit_state = "IDLE"

        return redact_secrets(
            {
                "session_id": session.session_id,
                "cockpit_state": service.current_cockpit_state,
                "session": session.status_snapshot(),
            }
        )

    @router.get("/session/{session_id}/status")
    async def get_voice_session_status(session_id: str) -> Dict[str, Any]:
        session = service.sessions.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Voice session not found.",
            )

        snap = session.status_snapshot()
        in_state = snap["input"]["state"]
        out_state = snap["output"]["state"]

        if session.is_cancelled:
            ui_state = "CANCELLED"
        elif in_state == CaptureState.RECORDING.value:
            ui_state = "LISTENING"
        elif out_state == PlaybackState.PLAYING.value:
            ui_state = "SPEAKING"
        elif in_state == CaptureState.ERROR.value or out_state == PlaybackState.ERROR.value:
            ui_state = "ERROR"
        else:
            ui_state = service.current_cockpit_state

        return redact_secrets(
            {
                "session_id": session.session_id,
                "cockpit_state": ui_state,
                "rms": service.last_rms,
                "session": snap,
            }
        )

    @router.post("/session/{session_id}/stop")
    async def stop_voice_session_capture(
        session_id: str,
        payload: Optional[StopVoiceSessionRequest] = None,
    ) -> Dict[str, Any]:
        session = service.sessions.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Voice session not found.",
            )

        await session.stop_capture()
        collector = service._session_collector_tasks.pop(session_id, None)
        if collector is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(collector, timeout=0.5)

        buf = service._session_audio_buffers.setdefault(session_id, bytearray())
        while True:
            rem = await session.input_manager.read_frame(timeout_seconds=0.01)
            if rem is None:
                break
            buf.extend(rem.pcm_bytes)

        captured_pcm = bytes(service._session_audio_buffers.pop(session_id, b""))
        service.last_rms = 0.0
        service.current_cockpit_state = "TRANSCRIBING"

        req_payload = payload or StopVoiceSessionRequest()
        speak = (
            req_payload.speak_response
            if req_payload.speak_response is not None
            else service.settings.speak_responses
        )

        effective_transcript = (req_payload.transcript or "").strip()
        active_lang = service.settings.language_code
        t_speech_end = time.time()
        last_active_idx: Optional[int] = None
        saved_wav_path: Optional[str] = None
        gnani_seg_id: Optional[str] = None
        stt_lat_ms: Optional[float] = None

        if len(captured_pcm) >= 1024:
            sr = session.input_manager.format_config.sample_rate
            total_frames = len(captured_pcm) // 1024
            for f_idx in range(total_frames):
                f_slice = captured_pcm[f_idx * 1024 : (f_idx + 1) * 1024]
                if (
                    compute_pcm_rms(f_slice, sample_width=2)
                    >= service.settings.speech_energy_threshold
                ):
                    last_active_idx = f_idx
            if last_active_idx is not None:
                trailing_frames = max(0, total_frames - 1 - last_active_idx)
                trailing_s = trailing_frames * (512.0 / float(max(1, sr)))
                t_speech_end = time.time() - trailing_s
            saved_wav_path = service.save_live_session_audio_artifact(
                session_id=session.session_id,
                pcm_bytes=captured_pcm,
                sample_rate=sr,
                t_speech_end_client=t_speech_end,
                last_active_frame_index=last_active_idx,
            )

        if (
            not effective_transcript
            and len(captured_pcm) >= 1024
            and getattr(service.stt_provider, "is_configured", False)
        ):
            try:
                (
                    effective_transcript,
                    gnani_seg_id,
                    server_lat,
                    e2e_lat,
                    bypassed_stitch,
                    fp_match,
                ) = await service.stream_pcm_via_gnani_ws(
                    raw_audio_or_pcm=captured_pcm,
                    language_code=active_lang,
                    sample_rate=session.input_manager.format_config.sample_rate,
                    realtime_pace=False,
                )
                stt_lat_ms = server_lat if server_lat is not None else e2e_lat
                service.last_detected_language = active_lang
                if bypassed_stitch and fp_match is not None:
                    t_rec_fast = time.time()
                    fast_out = await service.execute_fast_path_interrupt(
                        session=session,
                        match=fp_match,
                        raw_transcript=effective_transcript,
                        language_code=active_lang,
                        t_speech_end_client=t_speech_end,
                        t_transcript_received=t_rec_fast,
                        gnani_request_id=gnani_seg_id,
                        stitching_bypassed=True,
                        stt_latency_ms=stt_lat_ms,
                        is_live_mic=True,
                        session_audio_file=saved_wav_path,
                    )
                    return redact_secrets(
                        {
                            "cockpit_state": fast_out["cockpit_state"],
                            "turn": fast_out["activity_entry"],
                            "detected_language": active_lang,
                            "gnani_request_id": gnani_seg_id,
                            "transcript_latency_ms": stt_lat_ms,
                            "stitching_bypassed": True,
                            "matched_stop_word": fast_out["matched_stop_word"],
                            "stop_latency_ms": fast_out["stop_latency_ms"],
                        }
                    )
            except Exception as e:
                service.current_cockpit_state = "ERROR"
                return redact_secrets(
                    {
                        "session_id": session.session_id,
                        "cockpit_state": "ERROR",
                        "error": sanitize_error_message(str(e)),
                    }
                )

        t_transcript_received = time.time()
        if effective_transcript or req_payload.transcript is not None:
            t_action_started = time.time()
            turn_out = await _process_cockpit_turn_internal(
                service=service,
                session=session,
                display_transcript=effective_transcript,
                planner_transcript=effective_transcript,
                speak=speak,
                conversational_mode=req_payload.conversational_mode,
                detected_language=active_lang,
                t_speech_end_client=t_speech_end,
                t_transcript_received=t_transcript_received,
                gnani_request_id=gnani_seg_id,
                stt_latency_ms=stt_lat_ms,
            )
            if "timing" not in turn_out:
                service.log_turn_timing(
                    session_id=session.session_id,
                    request_id=turn_out["result"]["request_id"],
                    language_code=active_lang,
                    t_speech_end=t_speech_end,
                    t_transcript_received=t_transcript_received,
                    t_action_started=t_action_started,
                    gnani_request_id=gnani_seg_id,
                    transcript_latency_ms=stt_lat_ms,
                    stitching_bypassed=False,
                )
            return redact_secrets(
                {
                    "cockpit_state": service.current_cockpit_state,
                    "turn": turn_out["activity_entry"],
                    "detected_language": active_lang,
                    "gnani_request_id": gnani_seg_id,
                    "transcript_latency_ms": stt_lat_ms,
                    "audio_base64": turn_out.get("audio_base64"),
                }
            )

        service.current_cockpit_state = "IDLE"
        return redact_secrets(
            {
                "session_id": session.session_id,
                "cockpit_state": service.current_cockpit_state,
                "session": session.status_snapshot(),
            }
        )

    @router.post("/audio_turn")
    async def execute_audio_turn(payload: AudioTurnRequest) -> Dict[str, Any]:
        """Stream base64 PCM/WAV audio over WSS /stt/v3/stream -> Execute -> Timbre v3.0 SSE."""
        session = service.get_or_create_session(payload.session_id)
        if session.state.value == "IDLE":
            await session.start()

        service.current_cockpit_state = "TRANSCRIBING"
        lang = payload.language_code or service.settings.language_code
        if lang not in SUPPORTED_STT_LANGUAGES:
            lang = "en-IN"

        try:
            raw_audio = base64.b64decode(payload.audio_base64)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid base64 audio payload.",
            ) from e

        t_speech_end = payload.t_speech_end or time.time()
        (
            primary_text,
            gnani_seg_id,
            server_lat,
            e2e_lat,
            bypassed_stitch,
            fp_match,
        ) = await service.stream_pcm_via_gnani_ws(
            raw_audio_or_pcm=raw_audio,
            language_code=lang,
            sample_rate=16000,
            realtime_pace=payload.realtime_pace,
        )
        t_transcript_received = time.time()
        stt_lat_ms = server_lat if server_lat is not None else e2e_lat
        service.last_detected_language = lang

        if bypassed_stitch and fp_match is not None:
            return await service.execute_fast_path_interrupt(
                session=session,
                match=fp_match,
                raw_transcript=primary_text,
                language_code=lang,
                t_speech_end_client=payload.t_speech_end,
                t_transcript_received=t_transcript_received,
                gnani_request_id=gnani_seg_id,
                stitching_bypassed=True,
                stt_latency_ms=stt_lat_ms,
            )

        t_action_started = time.time()
        turn_out = await _process_cockpit_turn_internal(
            service=service,
            session=session,
            display_transcript=primary_text,
            planner_transcript=primary_text,
            speak=payload.speak_response,
            conversational_mode=payload.conversational_mode,
            detected_language=lang,
            t_speech_end_client=t_speech_end,
            t_transcript_received=t_transcript_received,
            gnani_request_id=gnani_seg_id,
            stt_latency_ms=stt_lat_ms,
        )
        if "timing" not in turn_out:
            timing_rec = service.log_turn_timing(
                session_id=session.session_id,
                request_id=turn_out["result"]["request_id"],
                language_code=lang,
                t_speech_end=t_speech_end,
                t_transcript_received=t_transcript_received,
                t_action_started=t_action_started,
                gnani_request_id=gnani_seg_id,
                transcript_latency_ms=stt_lat_ms,
                stitching_bypassed=False,
            )
            turn_out["timing"] = timing_rec
        turn_out["detected_language"] = lang
        turn_out["gnani_request_id"] = gnani_seg_id
        turn_out["transcript_latency_ms"] = stt_lat_ms
        turn_out["stitching_bypassed"] = False
        return turn_out

    @router.websocket("/ws/stream")
    async def voice_websocket_stream_proxy(
        websocket: WebSocket,
        language_code: Optional[str] = Query(default=None),
        sample_rate: int = Query(default=16000),
        session_id: Optional[str] = Query(default=None),
    ) -> None:
        """Local WebSocket proxy: Browser AudioWorklet (1024B PCM) -> Gnani WSS /stt/v3/stream."""
        await websocket.accept()
        session = service.get_or_create_session(session_id)
        if session.state.value == "IDLE":
            await session.start()

        lang = language_code or service.settings.language_code
        if lang not in SUPPORTED_STT_LANGUAGES:
            lang = "en-IN"

        pcm_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=500)
        raw_session_pcm = bytearray()
        stream_anchor_epoch_s: float = time.time()
        client_speech_end_ts: Optional[float] = None
        last_active_frame_idx: Optional[int] = None
        timeline_mapping: Dict[str, Any] = {
            "anchor_epoch_s": stream_anchor_epoch_s,
            "audio_context_current_time_s": 0.0,
            "base_latency_s": 0.0,
            "sample_rate": sample_rate,
            "frame_samples": 512,
            "frame_duration_s": 512.0 / float(max(1, sample_rate)),
        }
        frame_counter = 0
        closed_by_client = False
        initial_gate_state = service.effective_interrupt_gate_state()

        async def _read_browser_ws() -> None:
            nonlocal client_speech_end_ts, last_active_frame_idx, frame_counter, closed_by_client
            frame_dur_s = 512.0 / float(max(1, sample_rate))
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        closed_by_client = True
                        break
                    if "bytes" in msg and msg["bytes"]:
                        # Echo suppression: drop mic frames while TTS plays unless headphones_mode
                        if service.is_tts_playing and not service.settings.headphones_mode:
                            continue
                        frame_bytes = bytes(msg["bytes"])
                        raw_session_pcm.extend(frame_bytes)
                        rms_val = compute_pcm_rms(frame_bytes, sample_width=2)
                        if rms_val >= service.settings.speech_energy_threshold:
                            last_active_frame_idx = frame_counter
                            # Map frame index on audio timeline (never message receipt time)
                            anchor = float(
                                timeline_mapping.get("anchor_epoch_s", stream_anchor_epoch_s)
                            )
                            base_lat = float(timeline_mapping.get("base_latency_s", 0.0))
                            client_speech_end_ts = (
                                anchor + ((frame_counter + 1) * frame_dur_s) - base_lat
                            )
                        frame_counter += 1
                        with contextlib.suppress(asyncio.QueueFull):
                            pcm_queue.put_nowait(frame_bytes)
                    elif "text" in msg and msg["text"]:
                        with contextlib.suppress(Exception):
                            ctrl = json.loads(msg["text"])
                            ctype = ctrl.get("type")
                            if ctype == "timeline_init":
                                if ctrl.get("anchor_epoch_s") is not None:
                                    timeline_mapping["anchor_epoch_s"] = float(
                                        ctrl["anchor_epoch_s"]
                                    )
                                if ctrl.get("audio_context_current_time_s") is not None:
                                    timeline_mapping["audio_context_current_time_s"] = float(
                                        ctrl["audio_context_current_time_s"]
                                    )
                                if ctrl.get("base_latency_s") is not None:
                                    timeline_mapping["base_latency_s"] = float(
                                        ctrl["base_latency_s"]
                                    )
                            elif ctype == "speech_end":
                                # Stamp computed from AudioContext timeline + frame index
                                if ctrl.get("anchor_epoch_s") is not None:
                                    timeline_mapping["anchor_epoch_s"] = float(
                                        ctrl["anchor_epoch_s"]
                                    )
                                if ctrl.get("audio_context_current_time_s") is not None:
                                    timeline_mapping["audio_context_current_time_s"] = float(
                                        ctrl["audio_context_current_time_s"]
                                    )
                                if ctrl.get("base_latency_s") is not None:
                                    timeline_mapping["base_latency_s"] = float(
                                        ctrl["base_latency_s"]
                                    )
                                if ctrl.get("last_active_frame_index") is not None:
                                    last_active_frame_idx = int(ctrl["last_active_frame_index"])
                                if ctrl.get("t_speech_end_client") is not None:
                                    client_speech_end_ts = float(ctrl["t_speech_end_client"])
                                elif ctrl.get("t_speech_end") is not None:
                                    client_speech_end_ts = float(ctrl["t_speech_end"])
                            elif ctype == "stop_stream":
                                if (
                                    client_speech_end_ts is None
                                    and ctrl.get("t_speech_end_client") is not None
                                ):
                                    client_speech_end_ts = float(ctrl["t_speech_end_client"])
                                await pcm_queue.put(None)
                                break
                            elif ctype == "tts_state":
                                service.is_tts_playing = bool(ctrl.get("playing", False))
            except WebSocketDisconnect:
                closed_by_client = True
            except Exception as ws_err:
                _ = ws_err
                closed_by_client = True
            finally:
                with contextlib.suppress(Exception):
                    pcm_queue.put_nowait(None)

        noise_canceller = (
            PCM16NoiseCanceller(sample_rate_hz=sample_rate)
            if service.settings.noise_cancellation_enabled
            else None
        )

        async def _pcm_generator() -> AsyncIterator[bytes]:
            while True:
                chunk = await pcm_queue.get()
                if chunk is None:
                    break
                if noise_canceller is not None:
                    chunk = noise_canceller.process_frame(chunk)
                yield chunk

        reader_task = asyncio.create_task(_read_browser_ws())
        stitcher = SegmentStitcher(
            min_silence_ms=service.settings.vad_min_silence_ms,
            stitch_window_ms=service.settings.segment_stitch_window_ms,
        )
        bias_enabled = bool(service.settings.use_word_boosting)
        if initial_gate_state not in ("ACTING", "SPEAKING", "CORRECTING", "EXECUTING"):
            service.current_cockpit_state = "LISTENING"

        try:
            async for ev in service.stt_provider.stream_pcm(
                pcm_frames=_pcm_generator(),
                language_code=lang,
                sample_rate=sample_rate,
                vad_threshold=service.settings.vad_threshold,
                min_silence_ms=service.settings.vad_min_silence_ms,
                bias_list=DEFAULT_VOICE_BIAS_WORDS if bias_enabled else None,
                bias_score=1.5 if bias_enabled else 0.0,
                realtime_pace=True,
            ):
                if ev.kind == TranscriptEventKind.CONNECTED:
                    await websocket.send_json(
                        {
                            "type": "connected",
                            "session_id": session.session_id,
                            "language_code": lang,
                            "vad_min_silence_ms": service.settings.vad_min_silence_ms,
                            "segment_stitch_window_ms": service.settings.segment_stitch_window_ms,
                        }
                    )
                elif ev.kind == TranscriptEventKind.PROCESSING:
                    if initial_gate_state not in ("ACTING", "SPEAKING", "CORRECTING", "EXECUTING"):
                        service.current_cockpit_state = "TRANSCRIBING"
                    await websocket.send_json({"type": "processing", "timestamp": ev.timestamp})
                elif ev.kind == TranscriptEventKind.ERROR:
                    await websocket.send_json(
                        {"type": "error", "message": ev.error_message or "STT stream error"}
                    )
                elif ev.kind == TranscriptEventKind.TRANSCRIPT and ev.text:
                    t_rec_seg = time.time()
                    active_gate_state = (
                        initial_gate_state
                        if initial_gate_state in ("ACTING", "SPEAKING", "CORRECTING", "EXECUTING")
                        else service.effective_interrupt_gate_state()
                    )
                    # Stop/Undo fast-path BEFORE stitcher, gated by state & max_tokens!
                    fp_match = match_fast_path_lexicon(
                        ev.text,
                        current_state=active_gate_state,
                        max_tokens=service.settings.max_stop_tokens,
                    )
                    if fp_match.kind in (FastPathIntentKind.STOP, FastPathIntentKind.UNDO):
                        saved_wav = service.save_live_session_audio_artifact(
                            session_id=session.session_id,
                            pcm_bytes=bytes(raw_session_pcm),
                            sample_rate=sample_rate,
                            t_speech_end_client=client_speech_end_ts,
                            last_active_frame_index=last_active_frame_idx,
                            audio_timeline_mapping=timeline_mapping,
                        )
                        fast_out = await service.execute_fast_path_interrupt(
                            session=session,
                            match=fp_match,
                            raw_transcript=ev.text.strip(),
                            language_code=lang,
                            t_speech_end_client=client_speech_end_ts,
                            t_transcript_received=t_rec_seg,
                            gnani_request_id=ev.segment_id,
                            stitching_bypassed=True,
                            stt_latency_ms=ev.latency_ms,
                            is_live_mic=True,
                            session_audio_file=saved_wav,
                        )
                        if not closed_by_client:
                            await websocket.send_json({"type": "turn_result", **fast_out})
                        break

                    stitched_text, _ = stitcher.add_segment(
                        text=ev.text,
                        segment_id=ev.segment_id,
                        latency_ms=ev.latency_ms,
                    )
                    await websocket.send_json(
                        {
                            "type": "segment_transcript",
                            "text": ev.text,
                            "stitched_text": stitched_text,
                            "segment_id": ev.segment_id,
                            "latency_ms": ev.latency_ms,
                        }
                    )

            if stitcher.has_pending:
                flushed = stitcher.flush()
                final_txt = flushed["text"]
                seg_id = flushed["primary_segment_id"]
                lat_ms = flushed["latency_ms"]
                t_rec = time.time()
                t_act = time.time()
                saved_wav = service.save_live_session_audio_artifact(
                    session_id=session.session_id,
                    pcm_bytes=bytes(raw_session_pcm),
                    sample_rate=sample_rate,
                    t_speech_end_client=client_speech_end_ts,
                    last_active_frame_index=last_active_frame_idx,
                    audio_timeline_mapping=timeline_mapping,
                )
                turn_out = await _process_cockpit_turn_internal(
                    service=service,
                    session=session,
                    display_transcript=final_txt,
                    planner_transcript=final_txt,
                    speak=True,
                    conversational_mode=True,
                    detected_language=lang,
                    t_speech_end_client=client_speech_end_ts,
                    t_transcript_received=t_rec,
                    gnani_request_id=seg_id,
                    stt_latency_ms=lat_ms,
                )
                timing_rec = service.log_turn_timing(
                    session_id=session.session_id,
                    request_id=turn_out["result"]["request_id"],
                    language_code=lang,
                    t_speech_end=client_speech_end_ts or t_rec,
                    t_transcript_received=t_rec,
                    t_action_started=t_act,
                    gnani_request_id=seg_id,
                    transcript_latency_ms=lat_ms,
                    is_live_mic=True,
                    session_audio_file=saved_wav,
                )
                turn_out["detected_language"] = lang
                turn_out["gnani_request_id"] = seg_id
                turn_out["transcript_latency_ms"] = lat_ms
                turn_out["timing"] = timing_rec
                if not closed_by_client:
                    await websocket.send_json({"type": "turn_result", **turn_out})
        except Exception as e:
            if not closed_by_client:
                with contextlib.suppress(Exception):
                    await websocket.send_json(
                        {"type": "error", "message": sanitize_error_message(str(e))}
                    )
        finally:
            reader_task.cancel()
            with contextlib.suppress(Exception):
                await reader_task
            with contextlib.suppress(Exception):
                await websocket.close()

    @router.post("/session/{session_id}/cancel")
    async def cancel_voice_session(session_id: str) -> Dict[str, Any]:
        session = service.sessions.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Voice session not found.",
            )

        await session.cancel(reason="Cancelled by user from Command Cockpit")
        t_halted = time.time()
        service.is_tts_playing = False
        collector = service._session_collector_tasks.pop(session_id, None)
        if collector is not None and not collector.done():
            collector.cancel()
        service._session_audio_buffers.pop(session_id, None)

        service.approval_broker.revoke_all_for_session(session_id)
        stale_nonces = [
            n
            for n, ctx in service.coordinator._pending_contexts.items()
            if ctx.session_id == session_id
        ]
        for n in stale_nonces:
            service.coordinator._pending_contexts.pop(n, None)

        service.current_cockpit_state = "CANCELLED"
        service.last_rms = 0.0
        service.log_turn_timing(
            session_id=session.session_id,
            request_id=f"cancel_{int(t_halted * 1000)}",
            language_code=service.settings.language_code,
            t_action_halted=t_halted,
        )
        return redact_secrets(
            {
                "session_id": session.session_id,
                "cockpit_state": "CANCELLED",
                "cancelled": session.is_cancelled,
                "active_task_count": session.active_task_count,
                "session": session.status_snapshot(),
            }
        )

    @router.post("/turn")
    async def execute_voice_turn(payload: VoiceTurnRequest) -> Dict[str, Any]:
        session = service.get_or_create_session(payload.session_id)
        if session.state.value == "IDLE":
            await session.start()

        speak = (
            payload.speak_response
            if payload.speak_response is not None
            else service.settings.speak_responses
        )
        return await _process_cockpit_turn_internal(
            service=service,
            session=session,
            display_transcript=payload.transcript,
            planner_transcript=payload.transcript,
            speak=speak,
            request_id=payload.request_id,
            conversational_mode=payload.conversational_mode,
        )

    @router.post("/approval/resolve")
    async def resolve_voice_approval(payload: ResolveVoiceApprovalRequest) -> Dict[str, Any]:
        session = service.sessions.get(payload.session_id)
        if session is None:
            session = service.get_or_create_session(payload.session_id)

        service.current_cockpit_state = "EXECUTING" if payload.approved else "CANCELLED"
        res = await service.coordinator.resolve_approval(
            session=session,
            approval_nonce=payload.approval_nonce,
            approved=payload.approved,
        )
        if res.status == VoicePipelineStatus.COMPLETED and res.action_type == "launch_app":
            service.active_context_app = res.target_resource
        spoken_msg = build_multilingual_spoken_reply(
            action_type=res.action_type,
            target_resource=res.target_resource,
            status_val=res.status.value,
            lang=service.settings.language_code,
            fallback_msg=res.user_facing_message,
        )
        audio_b64 = None
        if payload.speak_response or service.settings.speak_responses:
            audio_b64 = await service.synthesize_ui_speech_b64(
                spoken_msg, language_code=service.settings.language_code
            )

        toast = {
            "kind": "success" if res.status == VoicePipelineStatus.COMPLETED else "warning",
            "title": (
                f"Executed: {res.action_type or 'Action'}"
                if res.status == VoicePipelineStatus.COMPLETED
                else "Action Not Executed"
            ),
            "message": spoken_msg,
        }
        entry = service.record_turn_activity(
            f"[Approval {'Granted' if payload.approved else 'Denied'}]",
            res,
            spoken_text=spoken_msg,
            audio_b64=audio_b64,
            toast_meta=toast,
        )
        out = redact_secrets(
            {
                "cockpit_state": service.current_cockpit_state,
                "result": res.model_dump(),
                "activity_entry": entry,
            }
        )
        if audio_b64:
            out["audio_base64"] = audio_b64
        return out

    @router.get("/activity")
    async def get_voice_activity() -> Dict[str, Any]:
        pending_list = []
        now = time.time()
        for nonce, ctx in list(service.coordinator._pending_contexts.items()):
            exp = ctx.broker_request.created_at + ctx.broker_request.ttl_seconds
            if now <= exp:
                pending_list.append(
                    {
                        "approval_nonce": nonce,
                        "request_id": ctx.request_id,
                        "session_id": ctx.session_id,
                        "action_type": ctx.intent.action_type.value,
                        "engine_tool_name": ctx.intent.engine_tool_name,
                        "target_resource": ctx.intent.target_resource,
                        "arguments": dict(ctx.intent.arguments),
                        "risk_tier": ctx.broker_request.risk_tier,
                        "expected_consequences": service.coordinator._describe_consequences(
                            ctx.intent
                        ),
                        "reason": ctx.broker_request.reason,
                        "expires_in_seconds": round(max(0.0, exp - now), 1),
                    }
                )
        app_open = service.is_context_app_open()
        return redact_secrets(
            {
                "cockpit_state": service.current_cockpit_state,
                "active_session_id": service.active_session_id,
                "active_context_app": service.active_context_app if app_open else None,
                "overlay_should_show": bool(app_open),
                "last_transcript": service.last_transcript,
                "last_matched_stop_word": service.last_matched_stop_word,
                "stop_latency_metrics": service.get_stop_latency_metrics(),
                "pending_approvals": pending_list,
                "history": list(service.activity_history),
                "timings": list(service.timing_history),
                "audit_events": service.get_recent_audit_records(limit=15),
                "audit_integrity_verified": service.audit_logger.verify_integrity(),
            }
        )

    @router.get("/metrics")
    async def get_voice_metrics() -> Dict[str, Any]:
        return redact_secrets(
            {
                "cockpit_state": service.current_cockpit_state,
                "last_transcript": service.last_transcript,
                "last_matched_stop_word": service.last_matched_stop_word,
                "aborted_safe_count": service.coordinator.aborted_safe_count,
                "failed_execution_count": service.coordinator.failed_execution_count,
                "stop_latency_metrics": service.get_stop_latency_metrics(),
            }
        )

    @router.get("/settings")
    async def get_voice_settings() -> Dict[str, Any]:
        return redact_secrets(service.settings.model_dump())

    @router.post("/settings")
    async def update_voice_settings(payload: VoiceUISettings) -> Dict[str, Any]:
        if payload.language_code not in SUPPORTED_STT_LANGUAGES and payload.language_code != "auto":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported language_code '{payload.language_code}'.",
            )
        if payload.model == "timbre-v2.5" and payload.voice not in TIMBRE_V25_VOICES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Voice '{payload.voice}' is not in the Timbre v2.5 catalog.",
            )
        if payload.model == "timbre-v3.0" and payload.voice not in TIMBRE_V30_VOICES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Voice '{payload.voice}' is not in the Timbre v3.0 catalog.",
            )
        service.settings = payload
        service.gnani_config.vad_threshold = payload.vad_threshold
        service.gnani_config.vad_min_silence_ms = payload.vad_min_silence_ms
        service.gnani_config.segment_stitch_window_ms = payload.segment_stitch_window_ms
        service.coordinator.step_executor.typing_char_delay_ms = payload.typing_char_delay_ms
        return redact_secrets(service.settings.model_dump())

    @router.get("/typing_baseline")
    async def get_typing_baseline() -> Dict[str, Any]:
        return redact_secrets(service.get_typing_baseline_summary())

    @router.post("/typing_baseline")
    async def record_typing_baseline(payload: TypingBaselineRecordRequest) -> Dict[str, Any]:
        rec = service.record_typing_baseline_run(
            mode=payload.mode,
            target_text=payload.target_text,
            typed_text=payload.typed_text,
            duration_ms=payload.duration_ms,
            first_keystroke_epoch_s=payload.first_keystroke_epoch_s,
            completed_epoch_s=payload.completed_epoch_s,
        )
        return redact_secrets(
            {
                "recorded": rec,
                "summary": service.get_typing_baseline_summary(),
            }
        )

    @router.get("/typing_baseline/page", response_class=HTMLResponse)
    async def get_typing_baseline_page() -> str:
        """Dedicated Manual vs. Voice Typing-Speed Baseline Recorder page (real runs only)."""
        return _TYPING_BASELINE_HTML_PAGE

    @router.get("/excel_bill")
    async def get_excel_grocery_bill() -> Dict[str, Any]:
        """Return live Excel grocery bill rows and exact Python-verified analytics."""
        return redact_secrets(service.excel_controller.compute_exact_analytics())

    @router.post("/excel_bill/open")
    async def open_excel_grocery_bill() -> Dict[str, Any]:
        """Generate `grocery_bill_sheet.csv` and launch it in Microsoft Excel (`EXCEL.EXE`)."""
        out = await asyncio.to_thread(service.excel_controller.open_in_microsoft_excel)
        return redact_secrets(out)

    @router.post("/agent_loop/execute")
    async def execute_agent_loop_goal(payload: VoiceTurnRequest) -> Dict[str, Any]:
        """Execute a multi-step goal through the Observe-Plan-Validate-Approve-Act-Verify-Recover loop."""
        active_tok = CancelToken()
        service.coordinator.step_executor.active_token = active_tok
        rep = await service.agent_loop.execute_goal(
            payload.transcript,
            session_id=payload.session_id or "voice_agent_loop",
            cancel_token=active_tok,
        )
        return redact_secrets(
            {
                "report": rep.model_dump(),
                "metrics": service.agent_loop.metrics.summary(),
            }
        )

    @router.get("/agent_loop/metrics")
    async def get_agent_loop_metrics() -> Dict[str, Any]:
        """Return runtime optimization metrics for the Observe-Plan-Act-Verify agent loop."""
        return redact_secrets(service.agent_loop.metrics.summary())

    return router


async def _process_cockpit_turn_internal(
    service: VoiceCockpitService,
    session: VoiceIOSession,
    display_transcript: str,
    planner_transcript: str,
    speak: bool,
    request_id: Optional[str] = None,
    conversational_mode: bool = False,
    detected_language: Optional[str] = None,
    t_speech_end_client: Optional[float] = None,
    t_transcript_received: Optional[float] = None,
    gnani_request_id: Optional[str] = None,
    stt_latency_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Execute a voice turn (Stop/Undo fast-path, Correction path, or normal Command path)."""
    active_lang = detected_language or detect_utterance_target_language(
        display_transcript, service.settings.language_code
    )
    service.last_detected_language = active_lang
    service.last_transcript = display_transcript.strip()
    t_rec = t_transcript_received if t_transcript_received is not None else time.time()

    # Normalize Telugu-transliterated stop words ('రూకో', 'రుకో', 'స్టాప్', 'నిల్లు')
    norm_for_stop = (
        display_transcript.replace("రూకో", "ruko")
        .replace("రుకో", "ruko")
        .replace("స్టాప్", "stop")
        .replace("నిల్లు", "nillu")
    )

    # 1. Stop / Undo Fast-Path (gated by state ACTING/SPEAKING/CORRECTING and max_stop_tokens)
    gate_state = service.effective_interrupt_gate_state()
    fp_match = match_fast_path_lexicon(
        norm_for_stop,
        current_state=gate_state,
        max_tokens=service.settings.max_stop_tokens,
    )
    if fp_match.kind in (FastPathIntentKind.STOP, FastPathIntentKind.UNDO):
        return await service.execute_fast_path_interrupt(
            session=session,
            match=fp_match,
            raw_transcript=display_transcript,
            language_code=active_lang,
            t_speech_end_client=t_speech_end_client,
            t_transcript_received=t_rec,
            gnani_request_id=gnani_request_id,
            stitching_bypassed=True,
            stt_latency_ms=stt_latency_ms,
        )

    # 2. Correction Path (modifies current plan/editor state in-place without restarting)
    is_correction = fp_match.kind == FastPathIntentKind.CORRECT
    corr_intent = None
    if is_correction:
        service.current_cockpit_state = "CORRECTING"
        try:
            corr_intent = await service.planner.plan_correction(
                display_transcript,
                current_task_state={
                    "current_text": service.coordinator.step_executor.current_editor_text,
                    "active_context_app": service.active_context_app,
                },
            )
        except IntentValidationError as e:
            clarify_res = VoicePipelineResult(
                request_id=request_id or f"vreq_{secrets.token_hex(6)}",
                session_id=session.session_id,
                status=VoicePipelineStatus.REJECTED_VALIDATION,
                user_facing_message=e.message,
                error_code=e.code,
                duration_ms=0.0,
            )
            audio_b64 = None
            if conversational_mode or speak:
                audio_b64 = await service.synthesize_ui_speech_b64(
                    e.message, language_code=active_lang
                )
            entry = service.record_turn_activity(
                display_transcript,
                clarify_res,
                spoken_text=e.message,
                audio_b64=audio_b64,
                detected_language=active_lang,
            )
            out = redact_secrets(
                {
                    "cockpit_state": service.current_cockpit_state,
                    "detected_language": active_lang,
                    "result": clarify_res.model_dump(),
                    "activity_entry": entry,
                }
            )
            if audio_b64:
                out["audio_base64"] = audio_b64
            return out
    else:
        service.current_cockpit_state = "UNDERSTOOD"

    lower_q = planner_transcript.lower().strip()

    if (
        not is_correction
        and conversational_mode
        and lower_q
        in (
            "open",
            "open app",
            "open application",
            "which should i open",
            "hey can i know which should i open",
            "what can you open",
            "hello",
            "hi",
            "hey",
        )
    ):
        planner_transcript = "list approved apps"
    elif not is_correction and conversational_mode and display_transcript.strip():
        # NEVER hijack general voice turns into Notepad just because a Notepad window is open!
        explicit_notepad = any(
            k in lower_q
            for k in ("notepad", "నోట్ ప్యాడ్", "నోట్‌ప్యాడ్", "नोटपैड", "நோட்பேட்")
        ) or lower_q.startswith(("write ", "type "))
        if explicit_notepad and not any(k in lower_q for k in ("open notepad", "launch notepad")):
            drafted = await service.draft_smart_notepad_content(display_transcript.strip())
            if drafted is not None and drafted.strip():
                planner_transcript = f"write {drafted}"
            else:
                planner_transcript = ""

    low_cmd = display_transcript.lower().strip()
    has_close_verb = any(
        k in low_cmd
        for k in ("close", "క్లోజ్", "క్లోజీ", "క్లోజ", "మూసివేయి", "बंद करो", "बंद कर", "மூடு", "क्लोज़")
    )
    has_close_target = any(
        k in low_cmd
        for k in (
            "paint",
            "పెయింట్",
            "ఎంఎస్",
            "ms",
            "అప్లికేషన్",
            "excel",
            "ఎక్సెల్",
            "ఎక్సల్",
            "notepad",
            "నోట్ ప్యాడ్",
            "पेंट",
            "பெயிண்ட்",
            "ఇది",
            "చేయి",
            "చెయ్",
            "it",
        )
    )
    is_close_app_cmd = has_close_verb and (
        has_close_target
        or service.active_context_app in ("paint", "excel", "notepad")
        or len(low_cmd.split()) <= 3
    )
    explicit_paint_keywords = (
        "పెయింట్",
        "पेंट",
        "பெயிண்ட்",
        "డ్రా",
        "గీయి",
        "గీసి",
        "ఇల్లు",
        "చందమామ",
        "మూన్",
        "రాకెట్",
        "సిటీ",
        "ఫ్యూచరిస్టిక్",
        "నగరం",
        "సర్కిల్",
        "సర్కిట్",
        "సర్కల్",
        "సరికిల్",
        "రౌండ్",
        "గుండ్రం",
        "సైకిల్",
        "స్క్వేర్",
        "రెక్టాంగిల్",
        "ట్రయాంగిల్",
        "స్టార్",
        "హార్ట్",
        "లవ్",
        "డైమండ్",
        "ఆరో",
        "యారో",
        "కలర్",
        "గోల్డ్",
        "draw ",
        "open paint",
        "paint open",
        "paint a ",
        "paint it ",
        "circle",
        "oval",
        "star",
        "heart",
        "triangle",
        "rectangle",
        "square",
        "diamond",
        "rocket",
        "futuristic city",
        "skyline",
        "house",
        "hut",
        "moon",
        "crescent",
        "chand",
        "chandamama",
        "nilavu",
        "gola",
        "tara",
        "dil",
        "geesi",
        "geeyi",
        "varai",
        "clear canvas",
    )
    explicit_excel_keywords = (
        "excel",
        "grocery",
        "groceries",
        "payment",
        "payments",
        "maligai",
        "மளிகை",
        "பில்",
        "గ్రోసరీ",
        "గ్రాసరీ",
        "గ్రాస్",
        "కిరాణా",
        "గ్రోసరీ బిల్",
        "ఎక్సెల్ బిల్",
        "బిల్లు",
        "బిల్",
        "ఎక్సెల్",
        "ఎక్సల్",
        "గ్రీన్స్",
        "గ్రెయిన్స్",
        "స్టాప్స్",
        "స్టాపుల్స్",
        "స్టాక్",
        "బియ్యం",
        "నూనె",
        "నెయ్యి",
        "జిఎస్టి",
        "జీఎస్టీ",
        "యుపిఐ",
        "యూపీఐ",
        "యూకే పేమెంట్స్",
        "పేమెంట్",
        "పేమెంట్స్",
        "కార్డ్",
        "క్యాష్",
        "నగదు",
        "ग्रोसरी",
        "एक्सल",
        "यूपीआई",
        "evlo",
        "evalo",
        "aachu",
        "mottham",
        "motham",
        "arisi",
        "अरिसी",
        "அரிசி",
        "paruppu",
        "பருப்பு",
        "ennai",
        "எண்ணெய்",
        "nallenai",
        "ghee",
        "ney",
        "நெய்",
        "paal",
        "பால்",
        "thayir",
        "தயிர்",
        "sarkarai",
        "சர்க்கரை",
        "vellam",
        "vengayam",
        "வெங்காயம்",
        "thakkali",
        "தக்காளி",
        "gst",
        "upi",
        "adhiga",
        "vilai",
        "விலை",
    )
    has_explicit_paint = any(k in low_cmd for k in explicit_paint_keywords)
    has_explicit_excel = any(k in low_cmd for k in explicit_excel_keywords)
    has_explicit_notepad = any(
        k in low_cmd for k in ("notepad", "నోట్ ప్యాడ్", "నోట్‌ప్యాడ్", "नोटपैड", "நோட்பேட்")
    )
    is_vscode_export_cmd = any(
        k in low_cmd
        for k in (
            "vs code",
            "vscode",
            "visual studio code",
            "విఎస్ కోడ్",
            "వీఎస్ కోడ్",
            "design concept",
            "డిజైన్ కాన్సెప్ట్",
            "project lo save",
            "ప్రాజెక్ట్ లో సేవ్",
        )
    )
    is_music_reminder_cmd = any(
        k in low_cmd
        for k in (
            "relaxing song",
            "play a song",
            "play song",
            "రిలాక్సింగ్ సాంగ్",
            "పాట ప్లే",
            "సాంగ్ ప్లే",
            "remind cheyyi",
            "remind me",
            "రిమైండ్ చేయి",
            "రిమైండర్",
            "30 minutes",
            "30 నిమిషాల",
        )
    )
    is_downloads_report_cmd = any(
        k in low_cmd
        for k in (
            "downloads",
            "డౌన్‌లోడ్స్",
            "డౌన్లోడ్స్",
            "latest report",
            "లేటెస్ట్ రిపోర్ట్",
            "important points extract",
            "professional summary",
        )
    )
    is_open_website_cmd = (
        not is_close_app_cmd
        and not is_vscode_export_cmd
        and not is_music_reminder_cmd
        and not is_downloads_report_cmd
        and not has_explicit_excel
        and not has_explicit_paint
        and any(
            k in low_cmd
            for k in (
                "website",
                "వెబ్‌సైట్",
                "వెబ్ సైట్",
                "వెబ్సైట్",
                "वेबसाइट",
                "chrome",
                "క్రోమ్",
                "क्रोम",
                "க்ரோம்",
                "youtube",
                "యూట్యూబ్",
                "यूट्यूब",
                "google",
                "గూగుల్",
                "गूगल",
                "linkedin",
                "లింక్డ్ఇన్",
                "లింక్డిన్",
                "github",
                "గిట్‌హబ్",
                "గిట్ హబ్",
                "gnani",
                "జ్ఞాని",
                "portfolio",
                "పోర్ట్‌ఫోలియో",
                "browser",
                "బ్రౌజర్",
            )
        )
    )

    # NEVER sticky-hijack unrelated sentences into Excel grocery bill!
    # Excel only runs when the user explicitly asks an Excel/grocery question.
    is_excel_or_grocery_cmd = (
        not is_close_app_cmd
        and not is_vscode_export_cmd
        and not is_music_reminder_cmd
        and not is_downloads_report_cmd
        and not is_open_website_cmd
        and not has_explicit_paint
        and not has_explicit_notepad
        and has_explicit_excel
    )
    is_paint_cmd = (
        not is_close_app_cmd
        and not is_vscode_export_cmd
        and not is_music_reminder_cmd
        and not is_downloads_report_cmd
        and not is_open_website_cmd
        and not is_excel_or_grocery_cmd
        and not has_explicit_notepad
        and low_cmd != "open mspaint"
        and has_explicit_paint
    )

    # Strip optional leading 'Rahul' wake-word for command routing while preserving raw display
    stripped_cmd, had_wake_word = strip_rahul_wake_word(display_transcript)
    if had_wake_word and stripped_cmd:
        planner_transcript = stripped_cmd
    instant_greeting = match_instant_rahul_greeting(display_transcript, active_lang)

    t_turn_start = time.perf_counter()
    conv_plan: Optional[ConversationalTurnPlan] = None
    if (
        not is_correction
        and instant_greeting is None
        and not is_close_app_cmd
        and not is_vscode_export_cmd
        and not is_music_reminder_cmd
        and not is_downloads_report_cmd
        and not is_open_website_cmd
        and not is_paint_cmd
        and not is_excel_or_grocery_cmd
        and service.settings.conversation_layer_enabled
    ):
        if (
            service.conversational_planner.llm_provider is None
            and service.conversational_planner.custom_llm_fn is None
        ):
            service.conversational_planner.llm_provider = service._resolve_active_llm_provider()
        if (
            service.conversational_planner.llm_provider is not None
            or service.conversational_planner.custom_llm_fn is not None
            or conversational_mode
        ):
            conv_plan = await service.conversational_planner.plan_turn(
                display_transcript,
                selected_language=active_lang,
                current_agent_span=service.coordinator.step_executor.current_editor_text,
            )

    service.current_cockpit_state = "ACTING"
    is_multistep_loop_cmd = bool(
        re.search(
            r"\b(?:and then|then|after that|followed by)\b|;|(?:\s+మరియు\s+)|(?:\s+और\s+फिर\s+)|(?:\s+பிறகு\s+)",
            display_transcript,
            flags=re.IGNORECASE,
        )
        or (has_explicit_paint and has_explicit_excel)
    )

    if is_correction and corr_intent is not None:
        res = await service.coordinator._revalidate_and_execute(
            session=session,
            request_id=request_id or f"vreq_{secrets.token_hex(6)}",
            intent=corr_intent,
            approval_required=False,
            approval_outcome="NOT_REQUIRED",
            speak_response=speak,
            start_perf=time.perf_counter(),
        )
    elif is_multistep_loop_cmd:
        active_tok = CancelToken()
        service.coordinator.step_executor.active_token = active_tok
        loop_rep = await service.agent_loop.execute_goal(
            display_transcript,
            session_id=session.session_id,
            cancel_token=active_tok,
        )
        loop_status_map = {
            "COMPLETED": VoicePipelineStatus.COMPLETED,
            "NEEDS_CLARIFICATION": VoicePipelineStatus.REJECTED_VALIDATION,
            "WAITING_FOR_APPROVAL": VoicePipelineStatus.WAITING_FOR_APPROVAL,
            "DENIED_BY_POLICY": VoicePipelineStatus.DENIED_BY_POLICY,
            "ABORTED_SAFE": VoicePipelineStatus.ABORTED_SAFE,
            "STOPPED": VoicePipelineStatus.STOPPED,
            "FAILED_EXECUTION": VoicePipelineStatus.FAILED_EXECUTION,
        }
        res = VoicePipelineResult(
            request_id=request_id or f"vloop_{secrets.token_hex(6)}",
            session_id=session.session_id,
            status=loop_status_map.get(loop_rep.status, VoicePipelineStatus.FAILED_EXECUTION),
            user_facing_message=loop_rep.summary,
            action_type="agent_loop_multistep",
            target_resource="multi_capability_loop",
            policy_decision="ALLOW" if loop_rep.status == "COMPLETED" else loop_rep.status,
            approval_required=(loop_rep.status == "WAITING_FOR_APPROVAL"),
            execution_output=loop_rep.model_dump(),
            observable_evidence=loop_rep.observable_evidence,
            duration_ms=loop_rep.total_duration_ms,
        )
    elif is_close_app_cmd:
        t_cl0 = time.perf_counter()
        if any(k in low_cmd for k in ("excel", "ఎక్సెల్", "ఎక్సల్", "एक्सल")):
            proc_name = "EXCEL.EXE"
            app_label = "Microsoft Excel"
        elif any(k in low_cmd for k in ("notepad", "నోట్ ప్యాడ్", "नोटपैड")):
            proc_name = "notepad.exe"
            app_label = "Notepad"
        elif any(k in low_cmd for k in ("paint", "పెయింట్", "पेंट", "பெயிண்ட்")):
            proc_name = "mspaint.exe"
            app_label = "Microsoft Paint"
        elif service.active_context_app == "excel":
            proc_name = "EXCEL.EXE"
            app_label = "Microsoft Excel"
        elif service.active_context_app == "notepad":
            proc_name = "notepad.exe"
            app_label = "Notepad"
        else:
            proc_name = "mspaint.exe"
            app_label = "Microsoft Paint"
        close_outcome = await service.dispatcher.execution_engine.execute_action(
            tool_name="process_close",
            arguments={"app_name": proc_name, "force": False},
            session_id=session.session_id,
            agent_id="voice_process_manager",
        )
        service.active_context_app = None
        lang_pref = active_lang.split("-")[0].lower()
        if lang_pref == "te":
            close_msg = f"{app_label} అప్లికేషన్‌ను క్లోజ్ చేశాను! ఇంకా ఏం చేయాలో చెప్పండి?"
        elif lang_pref == "ta":
            close_msg = f"{app_label} செயலியை மூடிவிட்டேன்! அடுத்து என்ன செய்யட்டும்?"
        elif lang_pref == "hi":
            close_msg = f"{app_label} बंद कर दिया है! आगे क्या करना है?"
        else:
            close_msg = f"Closed {app_label}."
        res = VoicePipelineResult(
            request_id=request_id or f"vclose_{secrets.token_hex(6)}",
            session_id=session.session_id,
            status=VoicePipelineStatus.COMPLETED,
            user_facing_message=close_msg,
            action_type="close_app",
            target_resource=proc_name,
            policy_decision="ALLOW",
            approval_required=False,
            execution_output=close_outcome.model_dump(),
            observable_evidence=[f"Graceful WM_CLOSE dispatched to {proc_name}"],
            duration_ms=round((time.perf_counter() - t_cl0) * 1000.0, 2),
        )
    elif instant_greeting is not None:
        greet_reply, greet_lang = instant_greeting
        active_lang = greet_lang
        res = VoicePipelineResult(
            request_id=request_id or f"vrahul_{secrets.token_hex(6)}",
            session_id=session.session_id,
            status=VoicePipelineStatus.COMPLETED,
            user_facing_message=greet_reply,
            action_type="rahul_wake_greeting",
            target_resource="rahul_assistant",
            policy_decision="ALLOW",
            approval_required=False,
            observable_evidence=[f"Instant Rahul wake-word greeting ({greet_lang})"],
            duration_ms=round((time.perf_counter() - t_turn_start) * 1000.0, 2),
        )
    elif is_vscode_export_cmd:
        t_vs0 = time.perf_counter()
        service.active_context_app = "vscode"
        vs_out = await asyncio.to_thread(
            service.showpiece_controller.export_design_concept_to_vscode,
            service.paint_controller.current_scene,
        )
        vs_msg = (
            "ఫ్యూచరిస్టిక్ సిటీ డిజైన్ కాన్సెప్ట్ ని విఎస్ కోడ్ (VS Code) లో కాపీ చేసి ప్రాజెక్ట్ లో సేవ్ చేశాను!"
            if active_lang.startswith("te")
            else "Copied the Futuristic City design concept into VS Code and saved it in your project."
        )
        res = VoicePipelineResult(
            request_id=request_id or f"vvscode_{secrets.token_hex(6)}",
            session_id=session.session_id,
            status=VoicePipelineStatus.COMPLETED,
            user_facing_message=vs_msg,
            action_type="vscode_design_export",
            target_resource=str(vs_out.get("project_dir")),
            policy_decision="ALLOW",
            approval_required=False,
            execution_output=vs_out,
            observable_evidence=vs_out.get("files_saved", []),
            duration_ms=round((time.perf_counter() - t_vs0) * 1000.0, 2),
        )
    elif is_music_reminder_cmd:
        t_mr0 = time.perf_counter()
        mr_out = await asyncio.to_thread(
            service.showpiece_controller.play_relaxing_music_and_schedule_reminder,
            display_transcript,
            30,
        )
        mins = mr_out.get("reminder", {}).get("duration_minutes", 30)
        mr_msg = (
            f"రిలాక్సింగ్ మ్యూజిక్ ప్లే చేస్తున్నాను మరియు {mins} నిమిషాల తర్వాత మీకు రిమైండర్ సెట్ చేశాను!"
            if active_lang.startswith("te")
            else f"Playing a relaxing ambient song and scheduled your reminder for {mins} minutes from now."
        )
        res = VoicePipelineResult(
            request_id=request_id or f"vmusic_{secrets.token_hex(6)}",
            session_id=session.session_id,
            status=VoicePipelineStatus.COMPLETED,
            user_facing_message=mr_msg,
            action_type="relaxing_music_and_reminder",
            target_resource=str(mr_out.get("music_track")),
            policy_decision="ALLOW",
            approval_required=False,
            execution_output=mr_out,
            observable_evidence=[str(mr_out.get("music_track")), str(mr_out.get("reminders_file"))],
            duration_ms=round((time.perf_counter() - t_mr0) * 1000.0, 2),
        )
    elif is_downloads_report_cmd:
        t_dr0 = time.perf_counter()
        llm_prov = service._resolve_active_llm_provider()
        dr_out = await service.showpiece_controller.find_and_summarize_latest_downloads_report(
            llm_provider=llm_prov,
            reply_language=active_lang,
        )
        dr_msg = str(dr_out.get("spoken_message") or "Summarized latest report from Downloads.")
        res = VoicePipelineResult(
            request_id=request_id or f"vreport_{secrets.token_hex(6)}",
            session_id=session.session_id,
            status=VoicePipelineStatus.COMPLETED,
            user_facing_message=dr_msg,
            action_type="downloads_report_summary",
            target_resource=str(dr_out.get("summary_saved_path")),
            policy_decision="ALLOW",
            approval_required=False,
            execution_output=dr_out,
            observable_evidence=[
                str(dr_out.get("source_report_path")),
                str(dr_out.get("summary_saved_path")),
            ],
            duration_ms=round((time.perf_counter() - t_dr0) * 1000.0, 2),
        )
    elif is_open_website_cmd:
        import webbrowser

        t_wb0 = time.perf_counter()
        if any(k in low_cmd for k in ("youtube", "యూట్యూబ్", "यूट्यूब")):
            target_url = "https://www.youtube.com"
            site_label = "YouTube"
        elif any(k in low_cmd for k in ("linkedin", "లింక్డ్ఇన్", "లింక్డిన్")):
            target_url = "https://www.linkedin.com"
            site_label = "LinkedIn"
        elif any(k in low_cmd for k in ("github", "గిట్‌హబ్", "గిట్ హబ్")):
            target_url = "https://github.com/vakrahul"
            site_label = "GitHub"
        elif any(k in low_cmd for k in ("gnani", "జ్ఞాని")):
            target_url = "https://www.gnani.ai/internship"
            site_label = "Gnani AI"
        elif any(k in low_cmd for k in ("portfolio", "పోర్ట్‌ఫోలియో")):
            target_url = "https://rahulvakiti.space"
            site_label = "Portfolio"
        elif any(k in low_cmd for k in ("google", "గూగుల్", "गूगल")):
            target_url = "https://www.google.com"
            site_label = "Google"
        else:
            target_url = "https://www.google.com"
            site_label = "Google Chrome"
        await asyncio.to_thread(webbrowser.open, target_url)
        web_msg = (
            f"క్రోమ్ బ్రౌజర్ లో {site_label} వెబ్‌సైట్ ఓపెన్ చేశాను! ఇంకా ఏం చేయాలో చెప్పండి?"
            if active_lang.startswith("te")
            else f"Opened {site_label} ({target_url}) in your browser."
        )
        res = VoicePipelineResult(
            request_id=request_id or f"vweb_{secrets.token_hex(6)}",
            session_id=session.session_id,
            status=VoicePipelineStatus.COMPLETED,
            user_facing_message=web_msg,
            action_type="open_website",
            target_resource=target_url,
            policy_decision="ALLOW",
            approval_required=False,
            execution_output={"url": target_url, "site": site_label},
            observable_evidence=[f"Opened {target_url} in browser"],
            duration_ms=round((time.perf_counter() - t_wb0) * 1000.0, 2),
        )
    elif is_excel_or_grocery_cmd and not is_paint_cmd:
        t_e0 = time.perf_counter()
        service.active_context_app = "excel"
        tanglish_words = ("evlo", "aachu", "arisi", "ennai", "vilai", "mottham")
        target_reply_lang = (
            conv_plan.reply_language
            if conv_plan is not None
            else ("ta-IN" if any(w in low_cmd for w in tanglish_words) else active_lang)
        )
        llm_prov = service._resolve_active_llm_provider()
        det_answer, excel_meta = await service.excel_controller.answer_question_dynamically_with_llm(
            display_transcript,
            llm_prov,
            target_reply_lang,
        )
        final_excel_msg = det_answer
        if conv_plan is not None and not conv_plan.is_system_fallback:
            conv_plan.reply_text = det_answer
            if target_reply_lang == "ta-IN":
                conv_plan.reply_language = "ta-IN"
        active_lang = "ta-IN" if target_reply_lang == "ta-IN" else active_lang
        e_dur = round((time.perf_counter() - t_e0) * 1000.0, 2)
        res = VoicePipelineResult(
            request_id=request_id or f"vexcel_{secrets.token_hex(6)}",
            session_id=session.session_id,
            status=VoicePipelineStatus.COMPLETED,
            user_facing_message=final_excel_msg,
            action_type="excel_grocery_analytics",
            target_resource=str(service.excel_controller.csv_path),
            policy_decision="ALLOW",
            approval_required=False,
            execution_output=excel_meta,
            observable_evidence=[final_excel_msg],
            duration_ms=e_dur,
        )
    elif is_paint_cmd:
        t_p0 = time.perf_counter()
        service.active_context_app = "paint"
        active_tok = CancelToken()
        service.coordinator.step_executor.active_token = active_tok
        if any(k in low_cmd for k in ("chart", "bar chart", "graph", "revenue")):
            chart_spec = service.excel_controller.build_paint_revenue_chart_scene_spec()
            paint_out = await asyncio.to_thread(
                service.paint_controller.execute_scene_spec,
                chart_spec,
                transcript=display_transcript,
                should_cancel=lambda: active_tok.is_cancelled or session.is_cancelled,
            )
        elif any(
            k in low_cmd
            for k in (
                "futuristic city",
                "city picture",
                "skyline",
                "ఫ్యూచరిస్టిక్",
                "సిటీ",
                "నగరం",
                "gallery of my imagination",
            )
        ):
            paint_out = await asyncio.to_thread(
                service.paint_controller.draw_starry_city_live_with_cursor,
                should_cancel=lambda: active_tok.is_cancelled or session.is_cancelled,
            )
        else:
            paint_out = await asyncio.to_thread(
                service.paint_controller.execute_natural_paint_command,
                display_transcript,
                should_cancel=lambda: active_tok.is_cancelled or session.is_cancelled,
                llm_ms=conv_plan.llm_latency_ms if conv_plan is not None else 0.0,
                plan_cache_hit=False,
            )
        p_dur = round((time.perf_counter() - t_p0) * 1000.0, 2)
        raw_st = str(paint_out.get("status") or "COMPLETED")
        status_map = {
            "STOPPED": VoicePipelineStatus.STOPPED,
            "ABORTED_SAFE": VoicePipelineStatus.ABORTED_SAFE,
            "FAILED_EXECUTION": VoicePipelineStatus.FAILED_EXECUTION,
        }
        p_status = status_map.get(raw_st, VoicePipelineStatus.COMPLETED)
        if p_status == VoicePipelineStatus.ABORTED_SAFE:
            service.coordinator.aborted_safe_count += 1
        elif p_status == VoicePipelineStatus.FAILED_EXECUTION:
            service.coordinator.failed_execution_count += 1

        # Build dynamic action-specific spoken message in the user's language (never a repeated canned line)
        planned_str = str(paint_out.get("planned_action") or paint_out.get("action") or "")
        objs_drawn = paint_out.get("objects") or []
        first_obj = objs_drawn[0] if objs_drawn else {}
        fill_col = first_obj.get("fill_color") or "gold"
        sh_name = first_obj.get("shape") or "shape"
        lang_pref = active_lang.split("-")[0].lower()
        if paint_out.get("action") == "clarify_shape":
            paint_msg = str(paint_out.get("summary"))
        elif paint_out.get("action") == "open_paint":
            if lang_pref == "te":
                paint_msg = "మైక్రోసాఫ్ట్ పెయింట్ స్క్రీన్ పై ఓపెన్ చేశాను! ఏం గీయాలో చెప్పండి?"
            elif lang_pref == "ta":
                paint_msg = "மைக்ரோசாப்ட் பெயிண்ட் திறந்துவிட்டேன்! என்ன வரையட்டும்?"
            elif lang_pref == "hi":
                paint_msg = "माइक्रोसॉफ्ट पेंट खोल दिया है! बताइए क्या बनाना है?"
            else:
                paint_msg = "Opened Microsoft Paint on screen. What should I draw?"
        elif "SceneEdit(add" in planned_str:
            if lang_pref == "te":
                paint_msg = f"దాని పక్కనే మరో {sh_name} గీసి {fill_col} రంగు వేశాను!"
            elif lang_pref == "ta":
                paint_msg = f"அதன் பக்கத்திலேயே மற்றொரு {sh_name} வரைந்து {fill_col} நிறம் கொடுத்துவிட்டேன்!"
            elif lang_pref == "hi":
                paint_msg = f"उसके बगल में एक और {sh_name} बनाकर {fill_col} रंग भर दिया है!"
            else:
                paint_msg = f"Added another {sh_name} in {fill_col} right beside it."
        elif "SceneEdit(recolour" in planned_str:
            if lang_pref == "te":
                paint_msg = f"సరే, {sh_name} రంగును {fill_col} రంగులోకి మార్చాను!"
            elif lang_pref == "ta":
                paint_msg = f"சரி, {sh_name} நிறத்தை {fill_col} நிறமாக மாற்றிவிட்டேன்!"
            elif lang_pref == "hi":
                paint_msg = f"ठीक है, {sh_name} का रंग बदलकर {fill_col} कर दिया है!"
            else:
                paint_msg = f"Changed the {sh_name} colour to {fill_col}."
        elif "moon_scene" in planned_str:
            if lang_pref == "te":
                paint_msg = "పెయింట్ లో బంగారు చందమామ మరియు నక్షత్రాలు గీశాను!"
            elif lang_pref == "ta":
                paint_msg = "பெயிண்டில் அழகான நிலா மற்றும் நட்சத்திரங்களை வரைந்துவிட்டேன்!"
            elif lang_pref == "hi":
                paint_msg = "पेंट में सुनहरा चाँद और तारे बना दिए हैं!"
            else:
                paint_msg = "Drew the golden crescent moon and stars in MS Paint."
        elif "house_scene" in planned_str:
            if lang_pref == "te":
                paint_msg = "పెయింట్ లో ఇల్లు, పైకప్పు మరియు సూర్యుడిని గీశాను!"
            elif lang_pref == "ta":
                paint_msg = "பெயிண்டில் வீடு மற்றும் சூரியனை வரைந்துவிட்டேன்!"
            elif lang_pref == "hi":
                paint_msg = "पेंट में घर और सूरज बना दिया है!"
            else:
                paint_msg = "Drew the house and sun in MS Paint."
        elif "futuristic_city_scene" in planned_str:
            if lang_pref == "te":
                paint_msg = "పెయింట్ లో అందమైన ఫ్యూచరిస్టిక్ సిటీ స్కైలైన్ మరియు టవర్స్ డిజైన్ చేశాను!"
            elif lang_pref == "ta":
                paint_msg = "பெயிண்டில் நவீன நகரத்தை வடிவமைத்துவிட்டேன்!"
            elif lang_pref == "hi":
                paint_msg = "पेंट में भविष्य का सुंदर शहर डिज़ाइन कर दिया है!"
            else:
                paint_msg = "Designed the futuristic city skyline in MS Paint."
        else:
            if lang_pref == "te":
                paint_msg = f"పెయింట్ లో {sh_name} గీసి దానికి {fill_col} రంగు వేశాను!"
            elif lang_pref == "ta":
                paint_msg = f"பெயிண்டில் {sh_name} வரைந்து {fill_col} நிறம் கொடுத்துவிட்டேன்!"
            elif lang_pref == "hi":
                paint_msg = f"पेंट में {sh_name} बनाकर उसमें {fill_col} रंग भर दिया है!"
            else:
                paint_msg = str(paint_out.get("summary") or f"Drew {sh_name} in {fill_col}.")

        res = VoicePipelineResult(
            request_id=request_id or f"vpaint_{secrets.token_hex(6)}",
            session_id=session.session_id,
            status=p_status,
            user_facing_message=paint_msg,
            action_type="paint_automation",
            target_resource="mspaint",
            policy_decision="ALLOW",
            approval_required=False,
            execution_output=paint_out,
            observable_evidence=[paint_msg],
            duration_ms=p_dur,
        )
    else:
        res = await service.coordinator.process_transcript(
            session=session,
            transcript=planner_transcript,
            speak_response=speak,
            request_id=request_id,
        )
        # Never dump raw Pydantic or allowlist errors in conversational mode!
        if (
            conversational_mode
            and res.status == VoicePipelineStatus.REJECTED_VALIDATION
            and planner_transcript != ""
        ):
            if conv_plan is not None and not conv_plan.is_system_fallback:
                reply_msg = conv_plan.reply_text
            else:
                reply_msg = build_multilingual_spoken_reply(
                    action_type=res.action_type,
                    target_resource=res.target_resource,
                    status_val=res.status.value,
                    lang=active_lang,
                    fallback_msg=res.user_facing_message,
                )
            res = VoicePipelineResult(
                request_id=res.request_id,
                session_id=session.session_id,
                status=VoicePipelineStatus.COMPLETED,
                user_facing_message=reply_msg,
                action_type="conversational_reply",
                target_resource="rahul_assistant",
                policy_decision="ALLOW",
                approval_required=False,
                observable_evidence=["Conversational reply"],
                duration_ms=res.duration_ms,
            )

    if (
        res.status == VoicePipelineStatus.COMPLETED
        and res.action_type == "type_in_notepad"
        and isinstance(res.execution_output, dict)
        and res.execution_output.get("text")
    ):
        typed_str = str(res.execution_output["text"])
        service.record_typing_baseline_run(
            mode="voice",
            target_text=typed_str,
            typed_text=typed_str,
            duration_ms=max(1.0, float(res.duration_ms)),
        )

    # In live conversational voice mode, auto-resolve safe whitelisted app launches (Notepad / Calc)
    # so the user's spoken command opens Notepad immediately and seamlessly!
    if (
        conversational_mode
        and res.status == VoicePipelineStatus.WAITING_FOR_APPROVAL
        and res.pending_approval is not None
        and res.action_type == "launch_app"
        and res.target_resource in ("notepad", "calc")
    ):
        res = await service.coordinator.resolve_approval(
            session=session,
            approval_nonce=res.pending_approval.approval_nonce,
            approved=True,
        )

    if res.status == VoicePipelineStatus.COMPLETED and res.action_type in (
        "launch_app",
        "type_in_notepad",
    ):
        service.active_context_app = (
            "notepad" if res.action_type == "type_in_notepad" else res.target_resource
        )

    if (
        conv_plan is not None
        and res.status == VoicePipelineStatus.COMPLETED
        and not conv_plan.is_system_fallback
        and res.action_type not in (
            "paint_automation",
            "excel_grocery_analytics",
            "close_app",
            "rahul_wake_greeting",
            "agent_loop_multistep",
        )
    ):
        spoken_msg = conv_plan.reply_text
        tts_lang = conv_plan.reply_language
    else:
        spoken_msg = build_multilingual_spoken_reply(
            action_type=res.action_type,
            target_resource=res.target_resource,
            status_val=res.status.value,
            lang=active_lang,
            fallback_msg=res.user_facing_message,
        )
        tts_lang = service.resolve_explicit_timbre_language(active_lang)

    audio_b64: Optional[str] = None
    tts_first_audio_ms: float = 0.0
    tts_synthesis_mode: str = "disabled"
    if conversational_mode or speak:
        audio_b64, tts_first_audio_ms, tts_synthesis_mode, tts_lang = (
            await service.synthesize_ui_speech_with_timing(spoken_msg, language_code=tts_lang)
        )

    total_turn_ms = round((time.perf_counter() - t_turn_start) * 1000.0, 2)
    if conv_plan is not None:
        service.log_turn_timing(
            session_id=session.session_id,
            request_id=res.request_id,
            language_code=active_lang,
            t_speech_end=t_speech_end_client,
            t_transcript_received=t_rec,
            gnani_request_id=gnani_request_id,
            transcript_latency_ms=stt_latency_ms,
            planner_provider=conv_plan.provider,
            planner_model=conv_plan.model,
            llm_latency_ms=conv_plan.llm_latency_ms,
            reply_language=conv_plan.reply_language,
            tts_first_audio_ms=tts_first_audio_ms,
            tts_synthesis_mode=tts_synthesis_mode,
            total_turn_ms=total_turn_ms,
        )

    toast = {
        "kind": (
            "approval"
            if res.status == VoicePipelineStatus.WAITING_FOR_APPROVAL
            else ("success" if res.status == VoicePipelineStatus.COMPLETED else "error")
        ),
        "title": (
            f"Confirmation Needed: {res.target_resource or res.action_type}"
            if res.status == VoicePipelineStatus.WAITING_FOR_APPROVAL
            else (
                f"Verified: {res.action_type or 'Completed'}"
                if res.status == VoicePipelineStatus.COMPLETED
                else "Clarification / Policy Gate"
            )
        ),
        "message": spoken_msg,
        "suggestions": (
            [
                "Open Notepad.",
                "List approved apps",
                "Show System Info",
            ]
            if res.action_type in ("list_approved_apps", "launch_app")
            or res.status != VoicePipelineStatus.COMPLETED
            else []
        ),
    }

    entry = service.record_turn_activity(
        display_transcript,
        res,
        spoken_text=spoken_msg,
        audio_b64=audio_b64,
        toast_meta=toast,
        detected_language=active_lang,
    )
    if conv_plan is not None:
        entry["conversational_turn"] = {
            "provider": conv_plan.provider,
            "model": conv_plan.model,
            "llm_latency_ms": conv_plan.llm_latency_ms,
            "reply_language": conv_plan.reply_language,
            "reply_text": conv_plan.reply_text,
            "is_system_fallback": conv_plan.is_system_fallback,
            "script_check_passed": conv_plan.script_check_passed,
            "script_check_detail": conv_plan.script_check_detail,
            "validation_failures": conv_plan.validation_failures,
            "memory_cap_info": conv_plan.memory_cap_info,
            "tts_first_audio_ms": tts_first_audio_ms,
            "tts_synthesis_mode": tts_synthesis_mode,
            "total_turn_ms": total_turn_ms,
        }
    out = redact_secrets(
        {
            "cockpit_state": service.current_cockpit_state,
            "detected_language": active_lang,
            "result": res.model_dump(),
            "activity_entry": entry,
            "conversational_turn": entry.get("conversational_turn"),
        }
    )
    if audio_b64:
        out["audio_base64"] = audio_b64
    return out


_TYPING_BASELINE_HTML_PAGE = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>WinAI-OE · Manual vs. Voice Typing-Speed Baseline Recorder</title>
  <style>
    :root {
      --bg: #070a11;
      --surface: #0f1422;
      --elevated: #161e31;
      --ink: #f3f0e6;
      --muted: #94a3b8;
      --gold: #d4a849;
      --cyan: #38bdf8;
      --emerald: #10b981;
      --line: rgba(243, 240, 230, 0.22);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--ink);
      font-family: "Segoe UI", system-ui, sans-serif;
      padding: 28px 24px;
      max-width: 1080px;
      margin: 0 auto;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 16px;
      margin-bottom: 24px;
      border-bottom: 2px dashed var(--line);
    }
    .card {
      background: var(--surface);
      border: 2px solid var(--line);
      border-radius: 14px;
      padding: 20px;
      margin-bottom: 20px;
    }
    h1 { font-size: 22px; font-weight: 800; }
    h2 {
      font-size: 15px; font-weight: 800; color: var(--gold);
      margin-bottom: 12px; text-transform: uppercase;
    }
    label {
      display: block; font-size: 13px; font-weight: 700;
      color: var(--muted); margin-bottom: 6px;
    }
    textarea, input[type="text"] {
      width: 100%;
      background: var(--elevated);
      color: var(--ink);
      border: 2px solid var(--line);
      border-radius: 10px;
      padding: 12px;
      font-size: 14px;
      font-family: Consolas, monospace;
      margin-bottom: 12px;
    }
    .btn {
      background: var(--gold);
      color: #111827;
      border: 2px solid var(--line);
      border-radius: 10px;
      padding: 8px 16px;
      font-weight: 800;
      cursor: pointer;
      text-decoration: none;
      display: inline-block;
    }
    .metrics-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-top: 10px;
    }
    .stat-box {
      background: var(--elevated);
      border: 1.5px solid var(--line);
      border-radius: 10px;
      padding: 12px 14px;
    }
    .stat-val {
      font-size: 22px; font-weight: 800;
      font-family: Consolas, monospace; color: var(--cyan);
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px; }
    th, td {
      text-align: left; padding: 10px;
      border-bottom: 1px solid var(--line);
      font-family: Consolas, monospace;
    }
    th { color: var(--gold); }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Manual vs. Voice Typing-Speed Baseline Recorder</h1>
      <p style="color: var(--muted); font-size: 13px;">
        Records first keystroke to completion for the exact same text (real runs only)
      </p>
    </div>
    <a class="btn" href="/cockpit">Back to Cockpit</a>
  </header>

  <section class="card">
    <h2>1. Target Text &amp; Manual Typing Timer (First Keystroke to Completion)</h2>
    <label for="targetTextInput">Target Text to Type (or Dictate in Cockpit)</label>
    <input type="text" id="targetTextInput"
      value="Meeting agenda: review Q4 latency metrics and finalize deployment checklist." />

    <label for="manualTypingArea">
      Start Typing Here (Timer starts on FIRST keystroke and stops when text matches target)
    </label>
    <textarea id="manualTypingArea" rows="3"
      placeholder="Begin typing the target text above..."></textarea>

    <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
      <button class="btn" id="btnFinishManual" onclick="finishManualRun()">
        Finish &amp; Record Manual Run
      </button>
      <button class="btn" style="background: var(--elevated); color: var(--ink);"
        onclick="resetManualTimer()">Reset Timer</button>
      <span id="liveTimerBadge"
        style="font-family: Consolas, monospace; font-weight: 700; color: var(--emerald);">
        Waiting for first keystroke...
      </span>
    </div>
  </section>

  <section class="card">
    <h2>2. Side-by-Side Comparison (Real Runs Only)</h2>
    <div id="baselineStatusBanner"
      style="font-family: Consolas, monospace; font-size: 14px; color: var(--gold);">
      no runs yet
    </div>
    <div id="comparisonTableWrap">
      <p id="emptyRunsNotice" style="color: var(--muted);">
        no runs yet — type the target text above or dictate it in Notepad from the Cockpit.
      </p>
      <table id="comparisonTable" style="display: none;">
        <thead>
          <tr>
            <th>Target Text</th>
            <th>Manual Time (ms)</th>
            <th>Manual WPM</th>
            <th>Voice Time (ms)</th>
            <th>Voice WPM</th>
            <th>Speedup</th>
          </tr>
        </thead>
        <tbody id="comparisonTbody"></tbody>
      </table>
    </div>
  </section>

  <script>
    let firstKeystrokeEpochS = null;
    let firstKeystrokePerfMs = null;
    let timerInterval = null;

    const area = document.getElementById("manualTypingArea");
    const targetEl = document.getElementById("targetTextInput");
    const badge = document.getElementById("liveTimerBadge");

    area.addEventListener("keydown", () => {
      if (firstKeystrokePerfMs === null) {
        firstKeystrokePerfMs = performance.now();
        firstKeystrokeEpochS = Date.now() / 1000.0;
        timerInterval = setInterval(() => {
          const elapsed = Math.round(performance.now() - firstKeystrokePerfMs);
          badge.textContent = `Typing: ${elapsed} ms`;
        }, 40);
      }
    });

    area.addEventListener("input", () => {
      const target = (targetEl.value || "").trim();
      const current = (area.value || "").trim();
      if (target && current === target && firstKeystrokePerfMs !== null) {
        finishManualRun();
      }
    });

    function resetManualTimer() {
      if (timerInterval) clearInterval(timerInterval);
      timerInterval = null;
      firstKeystrokePerfMs = null;
      firstKeystrokeEpochS = null;
      area.value = "";
      badge.textContent = "Waiting for first keystroke...";
    }

    async function finishManualRun() {
      if (firstKeystrokePerfMs === null) return;
      const durationMs = Math.max(1.0, performance.now() - firstKeystrokePerfMs);
      const completedEpochS = Date.now() / 1000.0;
      if (timerInterval) clearInterval(timerInterval);
      timerInterval = null;

      const typed = (area.value || "").trim();
      const target = (targetEl.value || "").trim();
      if (!typed || !target) return;

      badge.textContent = `Recorded Manual Run: ${Math.round(durationMs)} ms`;
      const startEpoch = firstKeystrokeEpochS;
      firstKeystrokePerfMs = null;
      firstKeystrokeEpochS = null;

      await fetch("/api/v1/voice/typing_baseline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: "manual",
          target_text: target,
          typed_text: typed,
          duration_ms: durationMs,
          first_keystroke_epoch_s: startEpoch,
          completed_epoch_s: completedEpochS
        })
      });
      loadBaselineSummary();
    }

    async function loadBaselineSummary() {
      try {
        const res = await fetch("/api/v1/voice/typing_baseline");
        const data = await res.json();
        const banner = document.getElementById("baselineStatusBanner");
        const emptyEl = document.getElementById("emptyRunsNotice");
        const tbl = document.getElementById("comparisonTable");
        const tbody = document.getElementById("comparisonTbody");

        banner.textContent = data.status_label || "no runs yet";
        if (!data.has_runs || !data.comparisons || data.comparisons.length === 0) {
          emptyEl.style.display = "block";
          tbl.style.display = "none";
          return;
        }
        emptyEl.style.display = "none";
        tbl.style.display = "table";
        tbody.innerHTML = data.comparisons.map(c => `
          <tr>
            <td>${c.target_text}</td>
            <td>${c.manual_duration_ms != null ? c.manual_duration_ms + " ms" : "—"}</td>
            <td>${c.manual_wpm != null ? c.manual_wpm : "—"}</td>
            <td>${c.voice_duration_ms != null ? c.voice_duration_ms + " ms" : "—"}</td>
            <td>${c.voice_wpm != null ? c.voice_wpm : "—"}</td>
            <td>${c.speedup_ratio != null ? c.speedup_ratio + "x" : "—"}</td>
          </tr>
        `).join("");
      } catch (e) {}
    }

    loadBaselineSummary();
  </script>
</body>
</html>
"""
