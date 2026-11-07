"""Phase 5 Real-Audio Interrupt & Command Evaluation Harness.

Streams recorded WAV files and ground-truth labels (`labels.csv`) through the
real Gnani Vachana STT WebSocket (`wss://api.vachana.ai/stt/v3/stream`) at
real-time frame pacing (1,024-byte PCM16 frames: 32 ms at 16 kHz, 64 ms at 8 kHz)
and evaluates the application's shipped fast-path matcher (`match_fast_path_lexicon`)
and state/token/negation gates without synthetic data.

Strict discipline:
1. `data/eval` is TRAINING. `data/eval_heldout` is HELD-OUT. They are evaluated
   and reported in separate tables, never merged.
2. Uses the app's real matcher and gates as shipped (`current_state="ACTING"`
   for `stop`/`undo`/`correct`/`none` rows, `current_state="LISTENING"` for
   `command` rows) and logs which gate (`gate_decision`) and rule (`matched_rule`)
   fired on every transcript segment.
3. Tracks `ABORTED_SAFE` (focus-guard aborts) distinct from `FAILED_EXECUTION`
   and reports counts of each.
4. Shows every miss and every false stop with all raw transcript segments, and
   emits lexicon suggestions only (never edits `src/voice/intent_schema.py`).
5. Validates the target dataset folder and `labels.csv` before streaming any
   audio; exits with a clear error message if validation fails.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import math
import struct
import sys
import time
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.storage.credential_vault import CredentialVault  # noqa: E402
from src.voice.audio_types import PCM16NoiseCanceller  # noqa: E402
from src.voice.base import (  # noqa: E402
    GnaniConfig,
    TranscriptEventKind,
)
from src.voice.gnani_stt import GnaniSTTProvider, SegmentStitcher  # noqa: E402
from src.voice.intent_schema import (  # noqa: E402
    FastPathIntentKind,
    FastPathMatch,
    VoiceIntentPlanner,
    match_fast_path_lexicon,
    normalize_utterance_for_lexicon,
)
from src.voice.pipeline import VoicePipelineStatus  # noqa: E402

SUPPORTED_EVAL_LANGUAGES: Set[str] = {"hi", "te", "ta", "en"}
VALID_EXPECTED_INTENTS: Set[str] = {"stop", "undo", "correct", "command", "none"}

LANG_SHORT_TO_GNANI_CODE: Dict[str, str] = {
    "hi": "hi-IN",
    "te": "te-IN",
    "ta": "ta-IN",
    "en": "en-IN",
    "hi-in": "hi-IN",
    "te-in": "te-IN",
    "ta-in": "ta-IN",
    "en-in": "en-IN",
}


@dataclass(frozen=True)
class EvalLabelRow:
    file: str
    language: str
    gnani_lang_code: str
    expected_intent: str
    expected_text: str
    condition: str
    wav_path: Path


@dataclass
class EvalFileResult:
    file: str
    language: str
    gnani_lang_code: str
    condition: str
    audio_condition: str
    expected_intent: str
    expected_text: str
    state_gate_used: str
    matched_intent: str
    correct: bool
    false_stop: bool
    gate_decision: str
    matched_rule: str
    matched_entry: str
    replacement_text: str
    transcript_segments: List[str] = field(default_factory=list)
    stitched_transcript: str = ""
    latency_ms: float = 0.0
    pipeline_status: str = VoicePipelineStatus.COMPLETED.value
    error: Optional[str] = None


def extract_pcm16_mono_from_wav(raw_wav_bytes: bytes) -> Tuple[bytes, int]:
    """Extract raw 16-bit signed little-endian mono PCM and sample_rate from WAV bytes."""
    with wave.open(io.BytesIO(raw_wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if sampwidth != 2:
        raise ValueError(f"Expected 16-bit PCM WAV (sampwidth=2), got sampwidth={sampwidth}")
    if channels == 1:
        return frames, rate
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    mono: List[int] = []
    for idx in range(0, len(samples), channels):
        grp = samples[idx : idx + channels]
        mono.append(int(sum(grp) // len(grp)))
    return struct.pack(f"<{len(mono)}h", *mono), rate


def wrap_pcm16_mono_to_wav(pcm_bytes: bytes, sample_rate_hz: int = 16000) -> bytes:
    """Wrap raw 16-bit mono PCM bytes into a standard RIFF WAVE container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate_hz)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def resolve_default_eval_dir(split: str) -> Path:
    """Resolve the default dataset directory for `training` or `heldout`."""
    split_norm = split.strip().lower()
    if split_norm == "heldout":
        candidates = [
            REPO_ROOT / "data" / "eval_heldout",
            Path(r"C:\Users\RAHUL\meo\data\eval_heldout"),
        ]
    else:
        candidates = [
            REPO_ROOT / "data" / "eval",
            Path(r"C:\Users\RAHUL\meo\data\eval"),
        ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def validate_eval_dataset(
    eval_dir: Path,
    *,
    allowed_languages: Optional[Set[str]] = None,
) -> Tuple[bool, str, List[EvalLabelRow]]:
    """Validate `eval_dir`, `labels.csv`, and all referenced WAV files before running."""
    langs = allowed_languages or SUPPORTED_EVAL_LANGUAGES
    if not eval_dir.exists() or not eval_dir.is_dir():
        return (
            False,
            (
                f"Evaluation dataset directory not found: {eval_dir}. "
                "Please place recorded WAV files and labels.csv (columns: "
                "file,language,expected_intent,expected_text,condition) in this directory."
            ),
            [],
        )

    labels_csv = eval_dir / "labels.csv"
    if not labels_csv.exists() or not labels_csv.is_file():
        return (
            False,
            (
                f"Missing labels.csv in {eval_dir}. Expected CSV with columns: "
                "file,language,expected_intent,expected_text,condition."
            ),
            [],
        )

    try:
        raw_text = labels_csv.read_text(encoding="utf-8-sig")
    except Exception as exc:
        return False, f"Failed to read {labels_csv}: {exc}", []

    reader = csv.DictReader(io.StringIO(raw_text))
    if not reader.fieldnames:
        return False, f"labels.csv in {eval_dir} has no header row.", []

    required_cols = {"file", "language", "expected_intent"}
    missing_cols = required_cols - set(h.strip() for h in reader.fieldnames if h)
    if missing_cols:
        return (
            False,
            f"labels.csv in {eval_dir} is missing required columns: {sorted(missing_cols)}",
            [],
        )

    validated_rows: List[EvalLabelRow] = []
    errors: List[str] = []

    for idx, row in enumerate(reader, start=2):
        file_rel = str(row.get("file") or "").strip()
        lang_raw = str(row.get("language") or "").strip().lower()
        expected_intent = str(row.get("expected_intent") or "").strip().lower()
        expected_text = str(row.get("expected_text") or "").strip()
        condition = str(row.get("condition") or "clean").strip()

        if not file_rel:
            errors.append(f"Row {idx}: empty 'file' column.")
            continue

        lang_short = lang_raw.split("-")[0]
        if lang_short not in langs:
            continue

        gnani_code = LANG_SHORT_TO_GNANI_CODE.get(lang_raw) or LANG_SHORT_TO_GNANI_CODE.get(
            lang_short
        )
        if not gnani_code:
            errors.append(f"Row {idx} ({file_rel}): unsupported language '{lang_raw}'.")
            continue

        if expected_intent not in VALID_EXPECTED_INTENTS:
            errors.append(
                f"Row {idx} ({file_rel}): invalid expected_intent '{expected_intent}' "
                f"(allowed: {sorted(VALID_EXPECTED_INTENTS)})."
            )
            continue

        wav_path = (eval_dir / file_rel).resolve()
        if not wav_path.exists() or not wav_path.is_file():
            errors.append(f"Row {idx}: audio file '{file_rel}' does not exist at {wav_path}.")
            continue

        try:
            with wave.open(str(wav_path), "rb") as wf:
                nframes = wf.getnframes()
                sampwidth = wf.getsampwidth()
                if nframes <= 0 or sampwidth != 2:
                    errors.append(
                        f"Row {idx} ({file_rel}): WAV must be non-empty 16-bit PCM "
                        f"(got nframes={nframes}, sampwidth={sampwidth})."
                    )
                    continue
        except Exception as exc:
            errors.append(f"Row {idx} ({file_rel}): invalid WAV file ({exc}).")
            continue

        validated_rows.append(
            EvalLabelRow(
                file=file_rel,
                language=lang_short,
                gnani_lang_code=gnani_code,
                expected_intent=expected_intent,
                expected_text=expected_text,
                condition=condition,
                wav_path=wav_path,
            )
        )

    if errors:
        preview = "\n  - ".join(errors[:10])
        return False, f"Dataset validation failed in {eval_dir}:\n  - {preview}", []

    if not validated_rows:
        return (
            False,
            f"No valid audio rows found in {labels_csv} for languages {sorted(langs)}.",
            [],
        )

    found_langs = sorted({r.language for r in validated_rows})
    return (
        True,
        f"Validated {len(validated_rows)} audio rows in {eval_dir} across languages {found_langs}.",
        validated_rows,
    )


def mix_pcm16_with_noise_and_resample(
    clean_pcm16: bytes,
    clean_rate_hz: int,
    *,
    noise_pcm16: Optional[bytes] = None,
    snr_db: Optional[float] = None,
    downsample_8khz: bool = False,
) -> Tuple[bytes, int, str]:
    """Optionally mix `clean_pcm16` with `noise_pcm16` at `snr_db` and downsample to 8 kHz."""
    samples = list(struct.unpack(f"<{len(clean_pcm16) // 2}h", clean_pcm16))
    cond_parts: List[str] = []

    if noise_pcm16 and snr_db is not None and len(noise_pcm16) >= 2 and samples:
        noise_samples = list(struct.unpack(f"<{len(noise_pcm16) // 2}h", noise_pcm16))
        sig_rms = math.sqrt(sum(float(s) * float(s) for s in samples) / len(samples))
        noise_rms = math.sqrt(sum(float(n) * float(n) for n in noise_samples) / len(noise_samples))
        if noise_rms > 1e-6 and sig_rms > 1e-6:
            target_noise_rms = sig_rms / (10.0 ** (float(snr_db) / 20.0))
            scale = target_noise_rms / noise_rms
            mixed: List[int] = []
            for idx, s in enumerate(samples):
                n = noise_samples[idx % len(noise_samples)]
                val = int(round(float(s) + float(n) * scale))
                mixed.append(max(-32768, min(32767, val)))
            samples = mixed
        cond_parts.append(f"snr_{snr_db:g}dB")
    else:
        cond_parts.append("clean")

    out_rate = clean_rate_hz
    if downsample_8khz and clean_rate_hz == 16000:
        downsampled: List[int] = []
        for idx in range(0, len(samples) - 1, 2):
            avg = (int(samples[idx]) + int(samples[idx + 1])) // 2
            downsampled.append(max(-32768, min(32767, avg)))
        samples = downsampled
        out_rate = 8000
        cond_parts.append("8kHz")
    else:
        cond_parts.append(f"{out_rate // 1000}kHz")

    out_pcm = struct.pack(f"<{len(samples)}h", *samples)
    return out_pcm, out_rate, "_".join(cond_parts)


async def _pcm_chunk_generator(pcm_bytes: bytes, chunk_size: int = 1024) -> AsyncIterator[bytes]:
    for offset in range(0, len(pcm_bytes), chunk_size):
        yield pcm_bytes[offset : offset + chunk_size]


def determine_state_gate_for_row(expected_intent: str) -> str:
    """Per Phase 5 rule 2: ACTING for stop/undo/correct/none rows, LISTENING for command rows."""
    if expected_intent.strip().lower() == "command":
        return "LISTENING"
    return "ACTING"


def classify_transcript_segments(
    segments: Sequence[str],
    stitched_text: str,
    *,
    expected_intent: str,
) -> Tuple[str, str, str, str, str]:
    """Run shipped `match_fast_path_lexicon` and `VoiceIntentPlanner` on transcript segments.

    Returns `(matched_intent, gate_decision, matched_rule, matched_entry, replacement_text)`.
    """
    state_gate = determine_state_gate_for_row(expected_intent)

    # 1. Check each raw segment before stitcher (exact behavior of VoiceCockpitService)
    last_fast_match: Optional[FastPathMatch] = None
    for seg in segments:
        seg_clean = (seg or "").strip()
        if not seg_clean:
            continue
        m = match_fast_path_lexicon(seg_clean, current_state=state_gate)
        last_fast_match = m
        if m.kind != FastPathIntentKind.NONE:
            return (
                m.kind.value,
                m.gate_decision,
                m.matched_rule or "none",
                m.matched_entry or "",
                m.normalized_transcript,
            )

    # 2. Check stitched transcript if no raw segment triggered early
    if stitched_text.strip():
        m = match_fast_path_lexicon(stitched_text, current_state=state_gate)
        last_fast_match = m
        if m.kind != FastPathIntentKind.NONE:
            return (
                m.kind.value,
                m.gate_decision,
                m.matched_rule or "none",
                m.matched_entry or "",
                m.normalized_transcript,
            )

    # 3. If state_gate == "LISTENING" (command rows) and no stop/undo fired:
    if state_gate == "LISTENING":
        full_text = stitched_text.strip()
        if not full_text:
            return ("none", "empty_transcript", "none", "", "")
        det_cmd = VoiceIntentPlanner._deterministic_interpret(full_text)
        if det_cmd is not None:
            act_val = (
                det_cmd.get("action_type")
                if isinstance(det_cmd, dict)
                else getattr(det_cmd, "action_type", "unknown")
            )
            return (
                "command",
                last_fast_match.gate_decision if last_fast_match else "gated_state_LISTENING",
                f"deterministic_command:{act_val}",
                full_text,
                "",
            )
        return (
            "command",
            last_fast_match.gate_decision if last_fast_match else "gated_state_LISTENING",
            "command_utterance_passed_listening_gate",
            full_text,
            "",
        )

    return (
        "none",
        last_fast_match.gate_decision if last_fast_match else "no_match",
        (last_fast_match.matched_rule or "none") if last_fast_match else "none",
        "",
        "",
    )


def build_lexicon_suggestions_for_misses(results: Sequence[EvalFileResult]) -> List[Dict[str, Any]]:
    """Inspect misses/false-stops and emit read-only lexicon/gate suggestions only."""
    suggestions: List[Dict[str, Any]] = []
    for r in results:
        if r.correct and not r.false_stop:
            continue
        _norm_str, tokens, norm_tokens = normalize_utterance_for_lexicon(r.stitched_transcript)
        if r.false_stop:
            suggestions.append(
                {
                    "file": r.file,
                    "language": r.language,
                    "issue": "false_stop",
                    "stitched_transcript": r.stitched_transcript,
                    "matched_entry": r.matched_entry,
                    "matched_rule": r.matched_rule,
                    "suggestion": (
                        f"Review false-stop trigger '{r.matched_entry}' via rule "
                        f"'{r.matched_rule}' in segment(s) {r.transcript_segments}; "
                        "consider stricter token context or exact match."
                    ),
                }
            )
        else:
            suggestions.append(
                {
                    "file": r.file,
                    "language": r.language,
                    "issue": f"missed_{r.expected_intent}",
                    "expected_text": r.expected_text,
                    "stitched_transcript": r.stitched_transcript,
                    "normalized_tokens": norm_tokens,
                    "gate_decision": r.gate_decision,
                    "suggestion": (
                        f"Candidate surface tokens from STT for '{r.expected_intent}' "
                        f"({r.language}): {tokens} (normalized: {norm_tokens}). "
                        "Do not auto-add; verify against held-out policy."
                    ),
                }
            )
    return suggestions


async def evaluate_single_row(
    row: EvalLabelRow,
    *,
    api_key: str,
    noise_pcm16: Optional[bytes] = None,
    snr_db: Optional[float] = None,
    downsample_8khz: bool = False,
    noise_cancellation: bool = True,
    reconnect_on_profile_switch: bool = False,
    trailing_silence_ms: int = 640,
) -> EvalFileResult:
    """Stream one WAV file through `wss://api.vachana.ai/stt/v3/stream` and classify it."""
    raw_wav = row.wav_path.read_bytes()
    clean_pcm, clean_rate = extract_pcm16_mono_from_wav(raw_wav)
    speech_pcm_len = len(clean_pcm)
    if trailing_silence_ms > 0:
        pad_samples = int((clean_rate * trailing_silence_ms) / 1000)
        clean_pcm = clean_pcm + (b"\x00\x00" * pad_samples)

    pcm_to_send, send_rate, audio_condition = mix_pcm16_with_noise_and_resample(
        clean_pcm,
        clean_rate,
        noise_pcm16=noise_pcm16,
        snr_db=snr_db,
        downsample_8khz=downsample_8khz,
    )
    if noise_cancellation:
        audio_condition = f"{audio_condition}_nc"
    speech_bytes_at_send_rate = int(round(speech_pcm_len * (send_rate / float(clean_rate))))

    state_gate = determine_state_gate_for_row(row.expected_intent)
    cfg = GnaniConfig.from_env_or_vault(
        api_key=api_key,
        reconnect_on_profile_switch=reconnect_on_profile_switch,
    )
    provider = GnaniSTTProvider(config=cfg)
    stitcher = SegmentStitcher(stitch_window_ms=cfg.segment_stitch_window_ms)
    canceller = PCM16NoiseCanceller(sample_rate_hz=send_rate) if noise_cancellation else None

    segments: List[str] = []
    t_first_frame_sent: Optional[float] = None
    t_last_speech_frame_sent: Optional[float] = None
    t_matched_or_final: Optional[float] = None
    error_msg: Optional[str] = None
    pipeline_status = VoicePipelineStatus.COMPLETED.value

    async def _timed_chunks() -> AsyncIterator[bytes]:
        nonlocal t_first_frame_sent, t_last_speech_frame_sent
        bytes_sent = 0
        async for chunk in _pcm_chunk_generator(pcm_to_send, 1024):
            now_send = time.perf_counter()
            if t_first_frame_sent is None:
                t_first_frame_sent = now_send
            bytes_sent += len(chunk)
            if bytes_sent <= speech_bytes_at_send_rate + 1024:
                t_last_speech_frame_sent = now_send
            if canceller is not None:
                chunk = canceller.process_frame(chunk)
            yield chunk

    t_start = time.perf_counter()
    try:
        async for ev in provider.stream_pcm(
            _timed_chunks(),
            language_code=row.gnani_lang_code,
            sample_rate=send_rate,
            realtime_pace=True,
        ):
            now_mono = time.perf_counter()
            if ev.kind == TranscriptEventKind.TRANSCRIPT and ev.text.strip():
                seg_text = ev.text.strip()
                segments.append(seg_text)
                stitcher.add_segment(seg_text, arrival_monotonic=now_mono)
                if t_matched_or_final is None:
                    check = match_fast_path_lexicon(seg_text, current_state=state_gate)
                    if check.kind != FastPathIntentKind.NONE:
                        t_matched_or_final = now_mono
            elif ev.kind == TranscriptEventKind.ERROR:
                error_msg = ev.error_message or ev.text
                pipeline_status = VoicePipelineStatus.FAILED_EXECUTION.value
    except Exception as exc:
        error_msg = str(exc)
        pipeline_status = VoicePipelineStatus.FAILED_EXECUTION.value

    t_end = time.perf_counter()
    stitched_text = stitcher.current_text().strip() or " ".join(segments).strip()

    matched_intent, gate_decision, matched_rule, matched_entry, replacement_text = (
        classify_transcript_segments(
            segments,
            stitched_text,
            expected_intent=row.expected_intent,
        )
    )

    is_correct = matched_intent == row.expected_intent
    is_false_stop = (row.expected_intent in {"none", "command"}) and (
        matched_intent in {"stop", "undo", "correct"}
    )
    ref_send_t = t_last_speech_frame_sent or t_first_frame_sent or t_start
    ref_recv_t = t_matched_or_final or t_end
    latency_ms = round(max(1.0, (ref_recv_t - ref_send_t) * 1000.0), 2)

    return EvalFileResult(
        file=row.file,
        language=row.language,
        gnani_lang_code=row.gnani_lang_code,
        condition=row.condition,
        audio_condition=audio_condition,
        expected_intent=row.expected_intent,
        expected_text=row.expected_text,
        state_gate_used=state_gate,
        matched_intent=matched_intent,
        correct=is_correct,
        false_stop=is_false_stop,
        gate_decision=gate_decision,
        matched_rule=matched_rule,
        matched_entry=matched_entry,
        replacement_text=replacement_text,
        transcript_segments=segments,
        stitched_transcript=stitched_text,
        latency_ms=latency_ms,
        pipeline_status=pipeline_status,
        error=error_msg,
    )


def compute_percentile(sorted_vals: Sequence[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return round(sorted_vals[low], 2)
    weight = rank - low
    return round(sorted_vals[low] * (1.0 - weight) + sorted_vals[high] * weight, 2)


def summarize_eval_results(
    split_name: str,
    eval_dir: Path,
    results: Sequence[EvalFileResult],
    *,
    noise_cancellation: bool = False,
    aborted_safe_count: int = 0,
    failed_execution_count: int = 0,
    reconnect_on_profile_switch: bool = False,
) -> Dict[str, Any]:
    """Compute per-language, per-condition, per-intent, and overall accuracy and false-stop rate."""
    by_lang: Dict[str, Dict[str, Any]] = {}
    for lang in sorted({r.language for r in results}):
        lang_rows = [r for r in results if r.language == lang]
        non_stop_rows = [r for r in lang_rows if r.expected_intent in {"none", "command"}]
        false_stops = [r for r in non_stop_rows if r.false_stop]
        correct_cnt = sum(1 for r in lang_rows if r.correct)
        by_lang[lang] = {
            "total": len(lang_rows),
            "correct": correct_cnt,
            "accuracy_pct": round((100.0 * correct_cnt / len(lang_rows)), 2) if lang_rows else 0.0,
            "non_stop_utterances": len(non_stop_rows),
            "false_stops": len(false_stops),
            "false_stop_rate_pct": (
                round((100.0 * len(false_stops) / len(non_stop_rows)), 2)
                if non_stop_rows
                else 0.0
            ),
        }

    total_rows = len(results)
    total_correct = sum(1 for r in results if r.correct)
    all_non_stop = [r for r in results if r.expected_intent in {"none", "command"}]
    all_false_stops = [r for r in all_non_stop if r.false_stop]

    status_failed = sum(
        1 for r in results if r.pipeline_status == VoicePipelineStatus.FAILED_EXECUTION.value
    )
    status_aborted_safe = sum(
        1 for r in results if r.pipeline_status == VoicePipelineStatus.ABORTED_SAFE.value
    )

    misses = [asdict(r) for r in results if not r.correct]
    false_stop_details = [asdict(r) for r in results if r.false_stop]
    suggestions = build_lexicon_suggestions_for_misses(results)

    # Per-condition breakdown (clean vs noisy)
    by_condition: Dict[str, Dict[str, Any]] = {}
    for cond in sorted({r.condition for r in results}):
        cond_rows = [r for r in results if r.condition == cond]
        non_stop_rows = [r for r in cond_rows if r.expected_intent in {"none", "command"}]
        false_stops = [r for r in non_stop_rows if r.false_stop]
        correct_cnt = sum(1 for r in cond_rows if r.correct)
        by_condition[cond] = {
            "total": len(cond_rows),
            "correct": correct_cnt,
            "accuracy_pct": round((100.0 * correct_cnt / len(cond_rows)), 2) if cond_rows else 0.0,
            "non_stop_utterances": len(non_stop_rows),
            "false_stops": len(false_stops),
            "false_stop_rate_pct": (
                round((100.0 * len(false_stops) / len(non_stop_rows)), 2)
                if non_stop_rows
                else 0.0
            ),
        }

    # Per-intent breakdown (stop, undo, correct, command, none)
    intent_order = ["stop", "undo", "correct", "command", "none"]
    by_intent: Dict[str, Dict[str, Any]] = {}
    for intent_key in intent_order:
        intent_rows = [r for r in results if r.expected_intent == intent_key]
        if not intent_rows:
            continue
        correct_cnt = sum(1 for r in intent_rows if r.correct)
        by_intent[intent_key] = {
            "total": len(intent_rows),
            "correct": correct_cnt,
            "accuracy_pct": round((100.0 * correct_cnt / len(intent_rows)), 2),
        }

    # 5x5 Confusion Matrix (Expected \ Matched)
    confusion_matrix: Dict[str, Dict[str, int]] = {
        exp: {pred: 0 for pred in intent_order} for exp in intent_order
    }
    for r in results:
        exp_k = r.expected_intent if r.expected_intent in confusion_matrix else "none"
        pred_k = r.matched_intent if r.matched_intent in intent_order else "none"
        confusion_matrix[exp_k][pred_k] += 1

    return {
        "split": split_name.upper(),
        "dataset_dir": str(eval_dir),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "noise_cancellation": bool(noise_cancellation),
        "connection_mode": (
            "reconnect_on_profile_switch"
            if reconnect_on_profile_switch
            else "single_connection_stitcher"
        ),
        "overall": {
            "total": total_rows,
            "correct": total_correct,
            "accuracy_pct": round((100.0 * total_correct / total_rows), 2) if total_rows else 0.0,
            "non_stop_utterances": len(all_non_stop),
            "false_stops": len(all_false_stops),
            "false_stop_rate_pct": (
                round((100.0 * len(all_false_stops) / len(all_non_stop)), 2)
                if all_non_stop
                else 0.0
            ),
            "aborted_safe_count": aborted_safe_count + status_aborted_safe,
            "failed_execution_count": failed_execution_count + status_failed,
        },
        "per_language": by_lang,
        "per_condition": by_condition,
        "per_intent": by_intent,
        "confusion_matrix": confusion_matrix,
        "misses": misses,
        "false_stops": false_stop_details,
        "lexicon_suggestions_only": suggestions,
        "rows": [asdict(r) for r in results],
    }


def render_markdown_report(summary: Dict[str, Any]) -> str:
    """Render a standalone Markdown table for either TRAINING or HELD-OUT (never merged)."""
    split_label = summary["split"]
    overall = summary["overall"]
    nc_flag = "ON" if summary.get("noise_cancellation") else "OFF"
    table_hdr = (
        "| Language | Correct / Total | Accuracy (%) | "
        "False Stops / Non-Stop | False-Stop Rate (%) |"
    )
    lines: List[str] = [
        f"# Phase 5 Interrupt & Command Evaluation — {split_label} SPLIT ONLY",
        "",
        f"- **Dataset Split:** `{split_label}` (`{summary['dataset_dir']}`)",
        f"- **Timestamp (UTC):** `{summary['timestamp_utc']}`",
        f"- **PCM16 Noise Cancellation (`--noise-cancellation`):** `{nc_flag}`",
        f"- **Connection Mode:** `{summary['connection_mode']}`",
        f"- **ABORTED_SAFE Count (Focus-Guard Aborts):** `{overall['aborted_safe_count']}`",
        f"- **FAILED_EXECUTION Count:** `{overall['failed_execution_count']}`",
        "- **Note on Latency:** WAV-replay evaluation measures intent accuracy and "
        "false-stop rate only; stop latency is measured exclusively from live microphone "
        "runs (`is_live_mic=True`).",
        "",
        f"## Per-Language & Overall Summary ({split_label})",
        "",
        table_hdr,
        "| :--- | :---: | :---: | :---: | :---: |",
    ]

    for lang, stats in summary["per_language"].items():
        lines.append(
            f"| `{lang}` | {stats['correct']} / {stats['total']} | {stats['accuracy_pct']:.2f}% | "
            f"{stats['false_stops']} / {stats['non_stop_utterances']} | "
            f"{stats['false_stop_rate_pct']:.2f}% |"
        )

    lines.append(
        f"| **OVERALL ({split_label})** | **{overall['correct']} / {overall['total']}** | "
        f"**{overall['accuracy_pct']:.2f}%** | "
        f"**{overall['false_stops']} / {overall['non_stop_utterances']}** | "
        f"**{overall['false_stop_rate_pct']:.2f}%** |"
    )

    # Per-condition table (clean vs noisy)
    cond_hdr = (
        "| Condition | Correct / Total | Accuracy (%) | "
        "False Stops / Non-Stop | False-Stop Rate (%) |"
    )
    lines.extend(
        [
            "",
            f"## Per-Condition Summary ({split_label}: Clean vs Noisy)",
            "",
            cond_hdr,
            "| :--- | :---: | :---: | :---: | :---: |",
        ]
    )
    for cond, stats in summary.get("per_condition", {}).items():
        lines.append(
            f"| `{cond}` | {stats['correct']} / {stats['total']} | {stats['accuracy_pct']:.2f}% | "
            f"{stats['false_stops']} / {stats['non_stop_utterances']} | "
            f"{stats['false_stop_rate_pct']:.2f}% |"
        )

    # Per-intent table
    intent_hdr = "| Expected Intent | Correct / Total | Accuracy (%) |"
    lines.extend(
        [
            "",
            f"## Per-Intent Summary ({split_label})",
            "",
            intent_hdr,
            "| :--- | :---: | :---: |",
        ]
    )
    for intent_k, stats in summary.get("per_intent", {}).items():
        lines.append(
            f"| `{intent_k}` | {stats['correct']} / {stats['total']} | "
            f"{stats['accuracy_pct']:.2f}% |"
        )

    # Confusion Matrix table
    intent_cols = ["stop", "undo", "correct", "command", "none"]
    cm = summary.get("confusion_matrix", {})
    lines.extend(
        [
            "",
            f"## Confusion Matrix ({split_label}: Expected \\\\ Matched)",
            "",
            "| Expected \\\\ Matched | `stop` | `undo` | `correct` | `command` | `none` |",
            "| :--- | :---: | :---: | :---: | :---: | :---: |",
        ]
    )
    for exp_k in intent_cols:
        row_counts = cm.get(exp_k, {})
        c_vals = [str(row_counts.get(col, 0)) for col in intent_cols]
        lines.append(f"| **`{exp_k}`** | " + " | ".join(c_vals) + " |")

    row_hdr = (
        "| File | Lang | Cond | State Gate | Expected | Matched | "
        "Gate Decision | Matched Rule | Segments |"
    )
    lines.extend(
        [
            "",
            "## Per-Utterance Gate & Rule Log",
            "",
            row_hdr,
            "| :--- | :---: | :---: | :---: | :---: | :---: | :--- | :--- | :--- |",
        ]
    )
    for r in summary["rows"]:
        segs_str = " / ".join(r["transcript_segments"]) if r["transcript_segments"] else "(empty)"
        lines.append(
            f"| `{r['file']}` | `{r['language']}` | `{r['audio_condition']}` | "
            f"`{r['state_gate_used']}` | `{r['expected_intent']}` | `{r['matched_intent']}` | "
            f"`{r['gate_decision']}` | `{r['matched_rule']}` | {segs_str} |"
        )

    lines.extend(["", "## Misses (With Raw Transcript Segments)", ""])
    if not summary["misses"]:
        lines.append("- None (0 misses).")
    else:
        for m in summary["misses"]:
            lines.append(
                f"- **`{m['file']}`** (`{m['language']}`, state=`{m['state_gate_used']}`): "
                f"expected `{m['expected_intent']}` (`\"{m['expected_text']}\"`), "
                f"matched `{m['matched_intent']}` | gate=`{m['gate_decision']}` | "
                f"rule=`{m['matched_rule']}` | segments=`{m['transcript_segments']}`"
            )

    lines.extend(["", "## False Stops (With Raw Transcript Segments)", ""])
    if not summary["false_stops"]:
        lines.append("- None (0 false stops).")
    else:
        for fs in summary["false_stops"]:
            lines.append(
                f"- **`{fs['file']}`** (`{fs['language']}`, state=`{fs['state_gate_used']}`): "
                f"expected `{fs['expected_intent']}` (`\"{fs['expected_text']}\"`), "
                f"falsely matched `{fs['matched_intent']}` on entry `\"{fs['matched_entry']}\"` | "
                f"gate=`{fs['gate_decision']}` | rule=`{fs['matched_rule']}` | "
                f"segments=`{fs['transcript_segments']}`"
            )

    lines.extend(["", "## Lexicon Suggestions Only (No Automatic Edits)", ""])
    if not summary["lexicon_suggestions_only"]:
        lines.append("- No lexicon changes suggested.")
    else:
        for s in summary["lexicon_suggestions_only"]:
            lines.append(f"- `{s['file']}` ({s['language']}): {s['suggestion']}")

    return "\n".join(lines) + "\n"


async def async_main(args: argparse.Namespace) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    split_name = args.split.strip().lower()
    if split_name not in {"training", "heldout"}:
        print(
            f"ERROR: --split must be 'training' or 'heldout' (got '{args.split}').",
            file=sys.stderr,
        )
        return 1

    eval_dir = (
        Path(args.eval_dir).resolve()
        if args.eval_dir
        else resolve_default_eval_dir(split_name)
    )
    ok, msg, rows = validate_eval_dataset(eval_dir)
    if not ok:
        print(f"VALIDATION ERROR: {msg}", file=sys.stderr)
        return 1
    print(f"[VALIDATED] {msg}")

    if args.files:
        wanted = {f.strip() for f in args.files.split(",") if f.strip()}
        rows = [r for r in rows if r.file in wanted]
        if not rows:
            print(
                f"ERROR: None of the requested --files ({wanted}) matched validated rows.",
                file=sys.stderr,
            )
            return 1

    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    noise_pcm16: Optional[bytes] = None
    if args.noise_wav:
        noise_path = Path(args.noise_wav).resolve()
        if not noise_path.exists():
            print(f"ERROR: --noise-wav file not found: {noise_path}", file=sys.stderr)
            return 1
        noise_pcm16, _ = extract_pcm16_mono_from_wav(noise_path.read_bytes())

    vault = CredentialVault()
    api_key = vault.get_credential("gnani")
    if not api_key:
        print(
            "ERROR: Gnani API key not found in Windows DPAPI CredentialVault (provider='gnani').",
            file=sys.stderr,
        )
        return 1

    use_nc = bool(args.noise_cancellation and not args.no_noise_cancellation)
    results: List[EvalFileResult] = []
    for idx, row in enumerate(rows, start=1):
        res = await evaluate_single_row(
            row,
            api_key=api_key,
            noise_pcm16=noise_pcm16,
            snr_db=args.snr_db,
            downsample_8khz=args.downsample_8khz,
            noise_cancellation=use_nc,
            reconnect_on_profile_switch=args.reconnect_on_profile_switch,
        )
        results.append(res)
        status_tag = "PASS" if res.correct else ("FALSE_STOP" if res.false_stop else "MISS")
        print(
            f"[{idx:02d}/{len(rows):02d}] {row.file} ({row.language}, "
            f"gate={res.state_gate_used}) -> expected={row.expected_intent}, "
            f"matched={res.matched_intent} [{status_tag}] "
            f"gate_decision={res.gate_decision} rule={res.matched_rule} "
            f"segments={res.transcript_segments}"
        )

    summary = summarize_eval_results(
        split_name=split_name,
        eval_dir=eval_dir,
        results=results,
        noise_cancellation=use_nc,
        aborted_safe_count=args.aborted_safe_count,
        failed_execution_count=args.failed_execution_count,
        reconnect_on_profile_switch=args.reconnect_on_profile_switch,
    )

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nc_slug = "nc_on" if use_nc else "nc_off"
    suffix = f"{split_name}_{nc_slug}_{ts_slug}"
    json_path = out_dir / f"{suffix}.json"
    md_path = out_dir / f"{suffix}.md"

    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    md_report = render_markdown_report(summary)
    md_path.write_text(md_report, encoding="utf-8")

    print("\n" + md_report)
    print(f"Saved JSON results to: {json_path}")
    print(f"Saved Markdown report to: {md_path}")
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 5 Real-Audio Interrupt & Command Evaluation Harness "
            "(TRAINING vs HELD-OUT separate)."
        )
    )
    parser.add_argument(
        "--split",
        default="training",
        choices=["training", "heldout"],
        help=(
            "Dataset split to evaluate ('training' uses data/eval, "
            "'heldout' uses data/eval_heldout)."
        ),
    )
    parser.add_argument(
        "--eval-dir",
        default=None,
        help="Optional explicit path to dataset folder containing WAV files and labels.csv.",
    )
    parser.add_argument(
        "--files",
        default=None,
        help="Optional comma-separated subset of WAV filenames from labels.csv to stream.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of rows from labels.csv to stream (0 = all).",
    )
    parser.add_argument(
        "--noise-wav",
        default=None,
        help="Optional path to a 16-bit PCM WAV noise file to mix at --snr-db.",
    )
    parser.add_argument(
        "--snr-db",
        type=float,
        default=None,
        help="Optional Signal-to-Noise Ratio in dB when mixing --noise-wav.",
    )
    parser.add_argument(
        "--downsample-8khz",
        action="store_true",
        help="Downsample audio to 8000 Hz before streaming over wss://api.vachana.ai/stt/v3/stream.",
    )
    parser.add_argument(
        "--noise-cancellation",
        action="store_true",
        help="Opt-in: enable PCM16 real-time noise cancellation filter during streaming.",
    )
    parser.add_argument(
        "--no-noise-cancellation",
        action="store_true",
        help="Disable PCM16 real-time noise cancellation filter during streaming.",
    )
    parser.add_argument(
        "--reconnect-on-profile-switch",
        action="store_true",
        help="Enable reconnect_on_profile_switch flag for A/B comparison against stitcher.",
    )
    parser.add_argument(
        "--aborted-safe-count",
        type=int,
        default=0,
        help="Recorded ABORTED_SAFE (focus-guard abort) count from coordinator runs.",
    )
    parser.add_argument(
        "--failed-execution-count",
        type=int,
        default=0,
        help="Recorded FAILED_EXECUTION count from coordinator runs to include in report.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(REPO_ROOT / "eval_results"),
        help="Directory where <split>_<timestamp>.json and .md reports are written.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
