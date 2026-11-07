#!/usr/bin/env python3
"""HELD-OUT guided recorder for the 40-phrase HELD-OUT interrupt/correction eval set.

Records 16 kHz mono 16-bit WAV files with your own voice and writes
data/eval_heldout/labels.csv in the format the eval script expects.

Setup:   pip install sounddevice numpy
Usage:   python scripts/record_heldout_set.py                 # record everything (resumes)
         python scripts/record_heldout_set.py --list          # print the prompt sheet only
         python scripts/record_heldout_set.py --start 26      # start from phrase 26
         python scripts/record_heldout_set.py --redo          # re-record existing files
         python scripts/record_heldout_set.py --list-devices  # show microphones
         python scripts/record_heldout_set.py --device 2      # pick a microphone
Notes:   Phrases 33-40 are the NOISY takes: turn on a fan, TV or street sound
         before recording them. Do not mix noise in later.
"""

from __future__ import annotations

import argparse
import csv
import sys
import wave
from pathlib import Path

SAMPLE_RATE = 16000
PAD_SECONDS = 0.6
MIN_PEAK = 0.03
CLIP_PEAK = 0.98
MIN_SECONDS = 0.3

# (number, language, intent, romanized text, native-script hint, condition)
ITEMS = [
    # ---- quiet takes (1-32) ----
    (1, "hi", "stop", "ek second ruk jao", "एक सेकंड रुक जाओ", "clean"),
    (2, "hi", "stop", "bas bas rehne do", "बस बस रहने दो", "clean"),
    (3, "te", "stop", "okka nimisham aapandi", "ఒక్క నిమిషం ఆపండి", "clean"),
    (4, "te", "stop", "vaddu vaddu aapeyyi", "వద్దు వద్దు ఆపేయి", "clean"),
    (5, "ta", "stop", "podhum niruthu", "போதும் நிறுத்து", "clean"),
    (6, "en", "stop", "okay that's enough stop now", "okay that's enough stop now", "clean"),
    (7, "hi", "undo", "woh line delete kar do", "वो लाइन डिलीट कर दो", "clean"),
    (8, "te", "undo", "aa maata teeseyandi", "ఆ మాట తీసేయండి", "clean"),
    (9, "ta", "undo", "adhai neekidu", "அதை நீக்கிடு", "clean"),
    (10, "en", "undo", "take that back", "take that back", "clean"),
    (
        11,
        "hi",
        "correct",
        "ise aur aasaan bhasha mein likho",
        "इसे और आसान भाषा में लिखो",
        "clean",
    ),
    (12, "hi", "correct", "thoda chhota karo", "थोड़ा छोटा करो", "clean"),
    (13, "te", "correct", "inkonchem sulabhanga raayi", "ఇంకొంచెం సులభంగా రాయి", "clean"),
    (14, "ta", "correct", "innum chinnadha ezhudhu", "இன்னும் சின்னதா எழுது", "clean"),
    (15, "en", "correct", "rewrite it more politely", "rewrite it more politely", "clean"),
    (
        16,
        "hi",
        "command",
        "notepad mein kal ki meeting ke baare mein do line likho",
        "नोटपैड में कल की मीटिंग के बारे में दो लाइन लिखो",
        "clean",
    ),
    (
        17,
        "hi",
        "command",
        "ek function banao jo do number jode",
        "एक फंक्शन बनाओ जो दो नंबर जोड़े",
        "clean",
    ),
    (
        18,
        "te",
        "command",
        "notepad lo repati meeting gurinchi raayi",
        "నోట్‌ప్యాడ్ లో రేపటి మీటింగ్ గురించి రాయి",
        "clean",
    ),
    (
        19,
        "ta",
        "command",
        "naalaiya meeting patthi rendu vari ezhudhu",
        "நாளைய மீட்டிங் பத்தி ரெண்டு வரி எழுது",
        "clean",
    ),
    (
        20,
        "en",
        "command",
        "write two lines about tomorrow's meeting",
        "write two lines about tomorrow's meeting",
        "clean",
    ),
    (
        21,
        "hi",
        "none",
        "bus nahi ruki isliye der ho gayi",
        "बस नहीं रुकी इसलिए देर हो गई",
        "clean",
    ),
    (22, "hi", "none", "aap mat ruko aage likhte jao", "आप मत रुको आगे लिखते जाओ", "clean"),
    (
        23,
        "hi",
        "none",
        "yeh vaakya full stop se khatam karo",
        "यह वाक्य फुल स्टॉप से खत्म करो",
        "clean",
    ),
    (
        24,
        "te",
        "none",
        "athanu aagakunda maatlaaduthune unnadu",
        "అతను ఆగకుండా మాట్లాడుతూనే ఉన్నాడు",
        "clean",
    ),
    (
        25,
        "te",
        "none",
        "eeroju bus stop daggara kaluddam",
        "ఈ రోజు బస్ స్టాప్ దగ్గర కలుద్దాం",
        "clean",
    ),
    (26, "te", "none", "aapakunda raastu undandi", "ఆపకుండా రాస్తూ ఉండండి", "clean"),
    (
        27,
        "te",
        "none",
        "repu udayam edu gantalaku lepandi",
        "రేపు ఉదయం ఏడు గంటలకు లేపండి",
        "clean",
    ),
    (
        28,
        "ta",
        "none",
        "avan niruthaama pesikitte irundhaan",
        "அவன் நிறுத்தாம பேசிக்கிட்டே இருந்தான்",
        "clean",
    ),
    (
        29,
        "ta",
        "none",
        "bus stop pakkathula sandhikkalaam",
        "பஸ் ஸ்டாப் பக்கத்துல சந்திக்கலாம்",
        "clean",
    ),
    (
        30,
        "ta",
        "none",
        "naalaikku kaalaila aaru manikku ezhuppu",
        "நாளைக்கு காலைல ஆறு மணிக்கு எழுப்பு",
        "clean",
    ),
    (31, "en", "none", "please don't stop typing", "please don't stop typing", "clean"),
    (
        32,
        "en",
        "none",
        "I forgot to pause the music earlier",
        "I forgot to pause the music earlier",
        "clean",
    ),
    # ---- noisy takes (33-40): record with fan / TV / street sound running ----
    (33, "hi", "stop", "thehro thehro", "ठहरो ठहरो", "noisy"),
    (34, "te", "stop", "aagandi aagandi", "ఆగండి ఆగండి", "noisy"),
    (35, "ta", "stop", "nillunga nillunga", "நில்லுங்க நில்லுங்க", "noisy"),
    (36, "en", "stop", "hold it hold it", "hold it hold it", "noisy"),
    (37, "en", "correct", "can you make that shorter", "can you make that shorter", "noisy"),
    (
        38,
        "hi",
        "command",
        "notepad mein good morning team likho",
        "नोटपैड में good morning team लिखो",
        "noisy",
    ),
    (
        39,
        "hi",
        "none",
        "thodi der baad likhna shuru karna",
        "थोड़ी देर बाद लिखना शुरू करना",
        "noisy",
    ),
    (40, "en", "none", "the next stop is Ameerpet", "the next stop is Ameerpet", "noisy"),
]


def filename(item):
    number, lang, intent, _text, _native, condition = item
    suffix = "_noisy" if condition == "noisy" else ""
    return f"h{number:02d}_{lang}_{intent}{suffix}.wav"


def print_sheet():
    print(f"{'#':>2}  {'lang':<4} {'intent':<8} {'cond':<6} say this")
    for item in ITEMS:
        number, lang, intent, text, native, condition = item
        print(f"{number:>2}  {lang:<4} {intent:<8} {condition:<6} {text}   [{native}]")


def write_labels(out_dir: Path) -> Path:
    path = out_dir / "labels.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "language", "expected_intent", "expected_text", "condition"])
        for item in ITEMS:
            _number, lang, intent, text, _native, condition = item
            writer.writerow([filename(item), lang, intent, text, condition])
    return path


def record_until_enter(device):
    import numpy as np
    import sounddevice as sd

    chunks = []

    def callback(indata, _frames, _time, _status):
        chunks.append(indata.copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=device, callback=callback
    ):
        input("   ...recording. Speak, then press Enter to stop. ")
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks)[:, 0]


def save_wav(path: Path, audio):
    import numpy as np

    pad = np.zeros(int(PAD_SECONDS * SAMPLE_RATE), dtype=np.float32)
    data = np.concatenate([pad, audio, pad])
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())


def play(audio, device=None):
    import sounddevice as sd

    _ = device
    sd.play(audio, SAMPLE_RATE)
    sd.wait()


def quality_warnings(audio):
    import numpy as np

    warnings = []
    if len(audio) / SAMPLE_RATE < MIN_SECONDS:
        warnings.append("very short recording")
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak < MIN_PEAK:
        warnings.append(f"too quiet (peak {peak:.3f}); move closer to the mic")
    if peak > CLIP_PEAK:
        warnings.append(f"clipping (peak {peak:.3f}); speak softer or move back")
    return warnings


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--out", default="data/eval_heldout", help="output folder (default data/eval_heldout)"
    )
    parser.add_argument("--list", action="store_true", help="print the prompt sheet and exit")
    parser.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    parser.add_argument("--device", default=None, help="input device index or name")
    parser.add_argument("--start", type=int, default=1, help="first phrase number to record")
    parser.add_argument("--redo", action="store_true", help="re-record files that already exist")
    args = parser.parse_args()

    if args.list:
        print_sheet()
        return 0
    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return 0

    device = args.device
    if device is not None and str(device).isdigit():
        device = int(device)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels_path = write_labels(out_dir)
    print(f"labels written to {labels_path}")
    print("Record in a quiet room, 30-40 cm from the mic, normal volume and speed.")
    print("Controls after each take: Enter = keep, r = redo, p = play back, s = skip.\n")

    noisy_notice_shown = False
    recorded = 0
    for item in ITEMS:
        number, lang, intent, text, native, condition = item
        if number < args.start:
            continue
        path = out_dir / filename(item)
        if path.exists() and not args.redo:
            print(f"[{number:02d}] {path.name} exists, skipping (use --redo to overwrite)")
            continue

        if condition == "noisy" and not noisy_notice_shown:
            print("\n=== NOISY TAKES (33-40): turn on a fan, TV or street sound NOW ===")
            input("Press Enter when the background noise is running. ")
            noisy_notice_shown = True

        while True:
            print(f"\n[{number:02d}/{len(ITEMS)}] {lang} | {intent} | {condition}")
            print(f"   SAY: {text}")
            print(f"   ({native})")
            input("   Press Enter to start recording. ")
            audio = record_until_enter(device)
            for warning in quality_warnings(audio):
                print(f"   WARNING: {warning}")
            action = input("   Enter = keep, r = redo, p = play, s = skip: ").strip().lower()
            while action == "p":
                play(audio)
                action = input("   Enter = keep, r = redo, s = skip: ").strip().lower()
            if action == "r":
                continue
            if action == "s":
                print("   skipped")
                break
            save_wav(path, audio)
            recorded += 1
            print(f"   saved {path.name}")
            break

    print(f"\nDone. Recorded {recorded} file(s) this session. Folder: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
