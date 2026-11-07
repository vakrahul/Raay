"""Waveform Stamp Verifier for Live Microphone Sessions (`scripts/check_session_stamp.py`).

Loads a `.winai/sessions/<session_id>_<ts>.wav` and its matching `.json` metadata file,
recomputes the RMS energy of every 1,024-byte (512-sample) frame directly from the WAV
waveform using the logged `speech_energy_threshold`, determines the last above-threshold
frame index and timeline timestamp (`t_speech_end_client`), and prints the difference
against the logged stamp.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.voice.audio_types import compute_pcm_rms
from src.voice.base import parse_wav_container


def verify_session_stamp(wav_path: Path, json_path: Optional[Path] = None) -> Dict[str, Any]:
    """Recompute the last above-threshold frame from `wav_path` and compare with `json_path`."""
    resolved_wav = wav_path.resolve()
    resolved_json = (json_path or resolved_wav.with_suffix(".json")).resolve()

    if not resolved_wav.exists():
        raise FileNotFoundError(f"Session WAV file not found: {resolved_wav}")
    if not resolved_json.exists():
        raise FileNotFoundError(f"Session JSON metadata file not found: {resolved_json}")

    raw_wav = resolved_wav.read_bytes()
    wav_meta = parse_wav_container(raw_wav)
    pcm_bytes = raw_wav[
        wav_meta.data_payload_offset : wav_meta.data_payload_offset + wav_meta.actual_data_bytes
    ]
    meta = json.loads(resolved_json.read_text(encoding="utf-8"))

    threshold = float(meta.get("speech_energy_threshold", 0.015))
    sample_rate = int(meta.get("sample_rate") or wav_meta.sample_rate or 16000)
    frame_bytes_len = 1024  # 512 samples * 2 bytes (16-bit mono)
    frame_duration_s = 512.0 / float(max(1, sample_rate))
    frame_duration_ms = frame_duration_s * 1000.0

    total_frames = len(pcm_bytes) // frame_bytes_len
    recomputed_last_active_idx: Optional[int] = None
    for idx in range(total_frames):
        frame = pcm_bytes[idx * frame_bytes_len : (idx + 1) * frame_bytes_len]
        rms = compute_pcm_rms(frame, sample_width=2)
        if rms >= threshold:
            recomputed_last_active_idx = idx

    recomputed_offset_ms = (
        round((recomputed_last_active_idx + 1) * frame_duration_ms, 2)
        if recomputed_last_active_idx is not None
        else None
    )

    logged_idx = meta.get("last_active_frame_index")
    logged_offset_ms = meta.get("last_active_frame_offset_ms")
    logged_stamp_s = meta.get("t_speech_end_client")

    timeline = meta.get("audio_timeline_mapping") or {}
    anchor_epoch_s = timeline.get("anchor_epoch_s")
    base_latency_s = float(timeline.get("base_latency_s", 0.0))

    recomputed_stamp_s: Optional[float] = None
    if anchor_epoch_s is not None and recomputed_last_active_idx is not None:
        recomputed_stamp_s = (
            float(anchor_epoch_s)
            + ((recomputed_last_active_idx + 1) * frame_duration_s)
            - base_latency_s
        )

    frame_index_diff = (
        (int(logged_idx) - int(recomputed_last_active_idx))
        if (logged_idx is not None and recomputed_last_active_idx is not None)
        else None
    )
    offset_diff_ms = (
        round(float(logged_offset_ms) - float(recomputed_offset_ms), 3)
        if (logged_offset_ms is not None and recomputed_offset_ms is not None)
        else None
    )
    stamp_diff_ms = (
        round((float(logged_stamp_s) - float(recomputed_stamp_s)) * 1000.0, 3)
        if (logged_stamp_s is not None and recomputed_stamp_s is not None)
        else None
    )

    return {
        "session_id": meta.get("session_id"),
        "wav_file": str(resolved_wav),
        "json_file": str(resolved_json),
        "sample_rate": sample_rate,
        "total_frames_1024": total_frames,
        "speech_energy_threshold": threshold,
        "logged_last_active_frame_index": logged_idx,
        "recomputed_last_active_frame_index": recomputed_last_active_idx,
        "frame_index_diff": frame_index_diff,
        "logged_offset_ms": logged_offset_ms,
        "recomputed_offset_ms": recomputed_offset_ms,
        "offset_diff_ms": offset_diff_ms,
        "logged_t_speech_end_client": logged_stamp_s,
        "recomputed_t_speech_end_client": recomputed_stamp_s,
        "stamp_diff_ms": stamp_diff_ms,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify .winai/sessions/ WAV waveform against logged t_speech_end_client stamp."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".winai/sessions",
        help="Path to a .wav/.json file or the .winai/sessions directory.",
    )
    args = parser.parse_args(argv)
    target_path = Path(args.target)

    wav_files: List[Path] = []
    if target_path.is_dir():
        wav_files = sorted(target_path.glob("*.wav"))
    elif target_path.suffix.lower() == ".json":
        wav_files = [target_path.with_suffix(".wav")]
    else:
        wav_files = [target_path]

    if not wav_files:
        print(f"No session WAV files found at: {target_path}")
        return 1

    for wf in wav_files:
        report = verify_session_stamp(wf)
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
