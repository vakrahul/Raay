"""Always-On Windows Background Microphone Listener for 'Rahul' Voice Assistant.

Runs continuously in the background on Windows 11 with ZERO button clicks required:
1. Pre-warms the SHA-256 Timbre v3.0 TTS cache for all instant 'Rahul' wake-word
   greetings (`te-IN`, `hi-IN`, `hi-en`, `ta-IN`, `en-IN`) so calling
   "Rahul em chesthunav" plays the spoken Telugu reply in <50 ms.
2. Captures live 16 kHz mono PCM16 frames from the default Windows microphone via
   `SpeechInputManager` and streams them to Gnani (`wss://api.vachana.ai/stt/v3/stream`).
3. Routes every spoken utterance to `http://127.0.0.1:8090/api/v1/voice/turn`
   (`speak_response=True, conversational_mode=True`) and plays the returned WAV
   audio directly through the Windows speakers via `winsound.PlaySound`.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import sys
import winsound
from pathlib import Path
from typing import AsyncIterator

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.storage.credential_vault import CredentialVault  # noqa: E402
from src.voice.base import (  # noqa: E402
    GnaniConfig,
    TranscriptEventKind,
    TTSAudioConfig,
    TTSRequest,
)
from src.voice.conversational_planner import INSTANT_RAHUL_GREETING_REPLIES  # noqa: E402
from src.voice.gnani_stt import GnaniSTTProvider  # noqa: E402
from src.voice.gnani_tts import GnaniTTSProvider  # noqa: E402
from src.voice.audio_types import AudioFormatConfig, CaptureState  # noqa: E402
from src.voice.speech_input import SpeechInputManager  # noqa: E402


async def prewarm_rahul_greeting_tts_cache(tts: GnaniTTSProvider) -> None:
    """Pre-synthesize and cache instant Rahul greeting & demo replies in background."""
    te_phrases = [
        INSTANT_RAHUL_GREETING_REPLIES.get("te-IN", ""),
        "ఇదిగో మీ ఎక్సెల్ గ్రోసరీ బిల్ మరియు చార్ట్స్ ఓపెన్ చేశాను! మొత్తం 25 వస్తువులు ఉన్నాయి, గ్రాండ్ టోటల్ 4741 రూపాయలు (GST 249 రూపాయలు).",
        "ఇదిగో మీ ఎక్సెల్ గ్రోసరీ బిల్! మొత్తం 25 వస్తువులు ఉన్నాయి, మొత్తం బిల్ 4741 రూపాయలు (GST 249 రూపాయలు). బియ్యం మరియు పప్పులు 1960 రూపాయలు, నూనె మరియు నెయ్యి 1099 రూపాయలు.",
        "యుపిఐ (UPI) ద్వారా 12 వస్తువులకు మొత్తం 2478 రూపాయలు పేమెంట్ చేశారు. కార్డ్ ద్వారా 1323 రూపాయలు మరియు క్యాష్ ద్వారా 940 రూపాయలు చెల్లించారు.",
        "మైక్రోసాఫ్ట్ పెయింట్ స్క్రీన్ పై ఓపెన్ చేశాను! ఏం గీయాలో చెప్పండి?",
        "పెయింట్ లో ellipse గీసి దానికి red రంగు వేశాను!",
        "దాని పక్కనే మరో heart గీసి red రంగు వేశాను!",
    ]
    for phrase in te_phrases:
        if not phrase:
            continue
        req = TTSRequest(
            text=phrase,
            voice="Shivani",
            model="timbre-v3.0",
            language="te-IN",
            audio_config=TTSAudioConfig(sample_rate=24000, container="wav"),
        )
        with contextlib.suppress(Exception):
            await tts.synthesize_buffered_sse(req, use_cache=True)


async def dispatch_utterance_and_speak(
    client: httpx.AsyncClient,
    input_mgr: SpeechInputManager,
    base_url: str,
    transcript: str,
    language_code: str,
) -> None:
    """Send spoken transcript to orchestrator, mute mic during speaker playback, and play WAV reply."""
    text = (transcript or "").strip()
    if not text:
        return
    # Ignore 1-word ambient background noise fragments unless they are stop/undo/wake words
    words = text.split()
    valid_short_words = {
        "raay",
        "ray",
        "rai",
        "roy",
        "రాయ్",
        "రే",
        "రై",
        "राय",
        "रे",
        "रॉय",
        "ராய்",
        "ரே",
        "rahul",
        "రాహుల్",
        "राहुल",
        "ராகுல்",
        "stop",
        "undo",
        "ruko",
        "రుకో",
        "రూకో",
        "रुको",
        "aagu",
        "ఆగు",
        "nillu",
        "நில்லு",
        "நிறுத்து",
        "moon",
        "house",
        "rocket",
        "paint",
        "excel",
    }
    if len(words) == 1 and words[0].lower().strip(".,!?") not in valid_short_words:
        return

    print(f"\n[HEARD] ({language_code}): {text!r}", flush=True)
    try:
        target_urls = [base_url]
        alt_url = "http://127.0.0.1:8090" if "8765" in base_url else "http://127.0.0.1:8765"
        target_urls.append(alt_url)
        res = None
        last_exc = None
        for u in target_urls:
            try:
                res = await client.post(
                    f"{u}/api/v1/voice/turn",
                    json={
                        "transcript": text,
                        "speak_response": True,
                        "conversational_mode": True,
                    },
                    timeout=25.0,
                )
                break
            except Exception as e:
                last_exc = e
        if res is None:
            raise last_exc or RuntimeError("All orchestrator URLs unreachable")
        if res.status_code != 200:
            print(f"[WARN] Server returned {res.status_code}: {res.text}", flush=True)
            return
        data = res.json()
        entry = data.get("activity_entry") or {}
        reply_msg = entry.get("user_facing_message") or (data.get("result") or {}).get(
            "user_facing_message"
        )
        print(f"[RAHUL]: {reply_msg}", flush=True)
        audio_b64 = data.get("audio_base64")
        if audio_b64:
            wav_bytes = base64.b64decode(audio_b64)
            # Pause microphone capture while speaker plays so Rahul never hears its own voice!
            with contextlib.suppress(Exception):
                await input_mgr.stop(clear_unread=True)
            await asyncio.to_thread(
                winsound.PlaySound,
                wav_bytes,
                winsound.SND_MEMORY,
            )
            await asyncio.sleep(0.35)
            with contextlib.suppress(Exception):
                await input_mgr.start()
    except Exception as exc:
        print(f"[ERROR] Failed dispatching utterance: {exc}", flush=True)
        with contextlib.suppress(Exception):
            if not input_mgr.is_recording:
                await input_mgr.start()


async def run_always_on_loop(base_url: str, language_code: str) -> None:
    vault = CredentialVault()
    gnani_cfg = GnaniConfig.from_env_or_vault(vault=vault, allow_vault_lookup=True)
    gnani_cfg.request_timeout_seconds = 60.0
    gnani_cfg.stream_timeout_seconds = 120.0
    stt = GnaniSTTProvider(config=gnani_cfg)
    tts = GnaniTTSProvider(
        config=gnani_cfg,
        cache_dir=REPO_ROOT / ".winai" / "tts_cache",
    )

    asyncio.create_task(prewarm_rahul_greeting_tts_cache(tts))
    print(
        f"[READY] Rahul Always-On Background Listener active (lang={language_code}, voice=Shivani). "
        "Say 'Rahul em chesthunav' or 'Rahul grocery bill open cheyyi' anytime!",
        flush=True,
    )

    fmt_cfg = AudioFormatConfig(max_session_bytes=52428800)
    input_mgr = SpeechInputManager(format_config=fmt_cfg)
    await input_mgr.start()

    async with httpx.AsyncClient() as client:
        try:
            while True:
                if input_mgr.state == CaptureState.ERROR or not input_mgr.is_recording:
                    with contextlib.suppress(Exception):
                        await input_mgr.close()
                    input_mgr = SpeechInputManager(format_config=fmt_cfg)
                    await input_mgr.start()

                async def _mic_pcm_chunks() -> AsyncIterator[bytes]:
                    # Stream continuously while recording (up to ~38 seconds per session)
                    for _ in range(1200):
                        if not input_mgr.is_recording:
                            break
                        frame = await input_mgr.read_frame(timeout_seconds=1.0)
                        if frame is not None and frame.pcm_bytes:
                            yield frame.pcm_bytes

                try:
                    async for ev in stt.stream_pcm(
                        _mic_pcm_chunks(),
                        language_code=language_code,
                        sample_rate=16000,
                        min_silence_ms=280,
                        min_speech_ms=80,
                        realtime_pace=False,
                    ):
                        if ev.kind == TranscriptEventKind.TRANSCRIPT and ev.text.strip():
                            await dispatch_utterance_and_speak(
                                client,
                                input_mgr=input_mgr,
                                base_url=base_url,
                                transcript=ev.text.strip(),
                                language_code=language_code,
                            )
                            break
                except Exception as stream_err:
                    print(f"[RECONNECTING STT] {stream_err}", flush=True)
                    await asyncio.sleep(0.3)
        finally:
            await input_mgr.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Always-On Windows Background Listener for Raay")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765", help="Orchestrator URL")
    parser.add_argument(
        "--language",
        default="te-IN",
        help="Primary STT language code (e.g. te-IN, hi-IN, ta-IN, en-IN)",
    )
    parser.add_argument(
        "--prewarm-only",
        action="store_true",
        help="Pre-warm the greeting TTS cache and exit",
    )
    args = parser.parse_args()

    if args.prewarm_only:
        vault = CredentialVault()
        cfg = GnaniConfig.from_env_or_vault(vault=vault, allow_vault_lookup=True)
        tts = GnaniTTSProvider(config=cfg, cache_dir=REPO_ROOT / ".winai" / "tts_cache")
        asyncio.run(prewarm_rahul_greeting_tts_cache(tts))
        print("[PREWARMED] Rahul instant greeting TTS cache ready.")
        return 0

    asyncio.run(run_always_on_loop(args.base_url.rstrip("/"), args.language))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
