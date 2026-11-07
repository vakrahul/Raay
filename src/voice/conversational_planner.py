"""Phase 6 Conversational Layer for WinAI-OE Voice Cockpit.

Provides a schema-validated, cancellable conversational turn planner that runs
ONLY after the deterministic stop/undo fast-path (`match_fast_path_lexicon`) has
passed without matching an interrupt.

Key Guarantees:
1. Never modifies `src/voice/intent_schema.py` or any stop/undo gates.
2. Calls the configured LLM provider with a strict system prompt (same language
   and script as the user's last utterance; Hinglish in -> Hinglish out; at most
   2 short sentences; digits for numbers; no Timbre tags except `<pauses>`).
3. Validates output against `ConversationalTurnPlan`:
   - `reply_language` in `{"hi-IN", "te-IN", "ta-IN", "en-IN", "hi-en"}` (never `"auto"`).
   - Truncates `reply_text` to at most 2 sentences before script checking.
   - Verifies script match per `reply_language`:
     * `hi-IN`: mostly Devanagari (`>= 50%` of letter characters), Latin loanwords allowed.
     * `te-IN`: Telugu script (`> 50%` of letter characters).
     * `ta-IN`: Tamil script (`> 50%` of letter characters).
     * `en-IN` and `hi-en`: Latin script (`>= 70%` of letter characters).
   - Retries the LLM once on validation failure, logging every validation failure
     and which rule fired (`validation_failures`).
   - Falls back to a fixed hardcoded `[System Message]` line in the user's
     selected language on timeout, empty response, or second validation failure.
4. Keeps conversation memory minimal: last 3 turns plus the current agent text
   span, capped by `max_context_tokens` (default 240 tokens), logging the cap.
5. Routes every item in `actions[]` through `validate_untrusted_intent`
   (`ValidatedVoiceIntent`), `SecurityPolicyEngine`, and `ApprovalBroker` unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from typing import Any, Callable, Coroutine, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from src.providers.base import BaseModelProvider, ChatMessage

logger = logging.getLogger(__name__)

ReplyLanguageCode = Literal["hi-IN", "te-IN", "ta-IN", "en-IN", "hi-en"]
ALLOWED_REPLY_LANGUAGES: Tuple[str, ...] = ("hi-IN", "te-IN", "ta-IN", "en-IN", "hi-en")

# Fixed hardcoded system fallback strings (never invented content)
SYSTEM_FALLBACK_MESSAGES: Dict[str, str] = {
    "en-IN": "[System Message] Command received and processed. How can I help next?",
    "hi-IN": "[System Message] आपका आदेश प्राप्त हुआ और पूरा किया गया।",
    "hi-en": "[System Message] Aapka command receive ho gaya hai.",
    "te-IN": "[System Message] మీ ఆదేశం స్వీకరించబడింది మరియు అమలు చేయబడింది.",
    "ta-IN": "[System Message] உங்கள் கட்டளை பெறப்பட்டு செயல்படுத்தப்பட்டது.",
}

# Common number words mapped to digits so TTS receives digits for numbers
_NUMBER_WORD_MAP: Dict[str, str] = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "एक": "1",
    "दो": "2",
    "तीन": "3",
    "चार": "4",
    "पांच": "5",
    "पाँच": "5",
    "एकटा": "1",
    "ఒక": "1",
    "రెండు": "2",
    "మూడు": "3",
    "நான்கு": "4",
    "ஒரு": "1",
    "இரண்டு": "2",
    "ரெண்டு": "2",
    "மூன்று": "3",
    "ek": "1",
    "do": "2",
    "teen": "3",
    "rendu": "2",
}

# Romanized Hindi / Hinglish markers for detecting Hinglish input in Latin script
_HINGLISH_LATIN_MARKERS = {
    "mein",
    "karo",
    "likho",
    "kholo",
    "banao",
    "ke",
    "ki",
    "ka",
    "aur",
    "hai",
    "hain",
    "kya",
    "kal",
    "aaj",
    "mujhe",
    "mera",
    "apna",
    "ek",
    "do",
    "nahi",
    "mat",
    "ruko",
    "wapas",
    "saaf",
    "thoda",
    "bada",
    "chhota",
}

# Romanized Telugu / Tenglish markers ("rahul em chesthunav", "ela unnav", "geeyi", "cheyyi")
_TENGLISH_LATIN_MARKERS = {
    "em",
    "emi",
    "chesthunav",
    "chestunnav",
    "chestunav",
    "chesthunnav",
    "cheshtuna",
    "cheshtunav",
    "ela",
    "unnav",
    "unav",
    "cheyyi",
    "chey",
    "geeyi",
    "geesi",
    "chesi",
    "raayi",
    "rayi",
    "enti",
    "ekkada",
    "veyyi",
    "marchu",
    "peddaga",
    "chinnaga",
    "illu",
    "chandamama",
}

# Romanized Tamil / Tanglish markers ("rahul enna panra", "evlo aachu", "mottham bill")
_TANGLISH_LATIN_MARKERS = {
    "enna",
    "panra",
    "panringa",
    "epdi",
    "irukka",
    "irukku",
    "varai",
    "varaindhu",
    "ezhudhu",
    "thira",
    "kodu",
    "niram",
    "perisa",
    "nilavu",
    "evlo",
    "evalo",
    "aachu",
    "achu",
    "mottham",
    "motham",
    "arisi",
    "ennai",
    "nallenai",
    "paruppu",
    "paal",
    "thayir",
    "sarkarai",
    "vellam",
    "vengayam",
    "thakkali",
    "adhiga",
    "athiga",
    "vilai",
    "kammi",
    "pannu",
    "panna",
    "sollu",
    "sollunga",
    "kanakku",
    "ethana",
    "porul",
    "maligai",
    "mattum",
    "matrum",
}

INSTANT_RAHUL_GREETING_REPLIES: Dict[str, str] = {
    "te-IN": "నేను సిద్ధంగా ఉన్నాను! మీ కోసం పెయింట్, ఎక్సెల్ లేదా ఏ పని అయినా వెంటనే చేస్తాను. ఏం చేయాలో చెప్పండి?",
    "hi-IN": "मैं बिल्कुल तैयार हूँ! बताइए, अभी पेंट या डेस्कटॉप पर क्या करना है?",
    "hi-en": "Main bilkul taiyaar hoon! Bataiye, abhi Paint ya desktop par kya karna hai?",
    "ta-IN": "நான் தயாராக இருக்கிறேன்! இப்போது என்ன வரைய வேண்டும் அல்லது திறக்க வேண்டும் என்று சொல்லுங்கள்?",
    "en-IN": "I am right here and ready! Tell me what to draw in Paint or open on your desktop.",
}


def strip_rahul_wake_word(text: str) -> Tuple[str, bool]:
    """Strip leading 'Raay'/'Rahul' wake-word prefix; returns `(cleaned_text, had_wake)`."""
    raw = (text or "").strip()
    if not raw:
        return "", False
    pattern = r"^(?:hey\s+|hi\s+|hello\s+|oy\s+|arey\s+)?(?:raay|ray|rai|roy|రాయ్|రే|రై|राय|रे|रॉय|ராய்|ரே|rahul|రాహుల్|राहुल|ராகுல்)[\s,!.:;-]*"
    m = re.match(pattern, raw, flags=re.IGNORECASE)
    if m:
        remainder = raw[m.end() :].strip()
        return (remainder if remainder else raw), True
    return raw, False


def match_instant_rahul_greeting(
    transcript: str,
    selected_language: str = "en-IN",
) -> Optional[Tuple[str, ReplyLanguageCode]]:
    """Return an immediate `<1ms` reply when user calls 'Raay' or 'Raay em chesthunav'."""
    raw = (transcript or "").strip()
    if not raw:
        return None
    low = re.sub(r"[^\w\s\u0900-\u097F\u0B80-\u0BFF\u0C00-\u0C7F]", " ", raw.lower())
    low = re.sub(r"\s+", " ", low).strip()

    # Check if it's a direct call to Raay/Rahul or status greeting ("em chesthunav", etc.)
    exact_wake_only = {
        "raay",
        "ray",
        "rai",
        "roy",
        "hey raay",
        "hey ray",
        "hi raay",
        "hi ray",
        "hello raay",
        "hello ray",
        "రాయ్",
        "రే",
        "రై",
        "राय",
        "रे",
        "रॉय",
        "ராய்",
        "ரே",
        "rahul",
        "hey rahul",
        "hi rahul",
        "hello rahul",
        "రాహుల్",
        "राहुल",
        "ராகுல்",
    }
    status_greeting_phrases = (
        "em chesthunav",
        "em chestunnav",
        "em chestunav",
        "em cheshtuna",
        "em cheshtunav",
        "ela unnav",
        "ela unav",
        "ఏం చేస్తున్నావ్",
        "ఏం చేస్తున్నావు",
        "ఎలా ఉన్నావ్",
        "kya kar rahe ho",
        "kya hal hai",
        "क्या कर रहे हो",
        "enna panra",
        "enna panringa",
        "என்ன பண்ற",
        "what are you doing",
        "how are you",
        "are you there",
    )
    # Must not contain an explicit desktop or Excel/grocery command word
    command_verbs = (
        "draw",
        "paint",
        "circle",
        "star",
        "heart",
        "house",
        "rocket",
        "moon",
        "open",
        "write",
        "type",
        "clear",
        "undo",
        "stop",
        "ruko",
        "aagu",
        "nillu",
        "geeyi",
        "geesi",
        "banao",
        "varai",
        "excel",
        "grocery",
        "bill",
        "evlo",
        "aachu",
        "arisi",
        "oil",
        "ennai",
        "gst",
        "upi",
        "costly",
        "vilai",
        "mottham",
        "total",
    )
    if any(v in low.split() for v in command_verbs):
        return None

    if low in exact_wake_only or any(p in low for p in status_greeting_phrases):
        target_lang = detect_utterance_target_language(raw, selected_language)
        reply = INSTANT_RAHUL_GREETING_REPLIES.get(
            target_lang, INSTANT_RAHUL_GREETING_REPLIES["en-IN"]
        )
        return reply, target_lang
    return None


class ConversationalMemoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_transcript: str
    reply_text: str
    reply_language: str


class ConversationalMemoryBuffer:
    """Minimal conversation memory: last 3 turns + current agent text span, capped by tokens."""

    def __init__(self, max_turns: int = 3, max_context_tokens: int = 240) -> None:
        self.max_turns = max(1, int(max_turns))
        self.max_context_tokens = max(32, int(max_context_tokens))
        self._turns: List[ConversationalMemoryTurn] = []

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        words = [w for w in re.split(r"\s+", text.strip()) if w]
        return max(len(words), int(len(text) / 4))

    def add_turn(self, user_transcript: str, reply_text: str, reply_language: str) -> None:
        self._turns.append(
            ConversationalMemoryTurn(
                user_transcript=user_transcript.strip(),
                reply_text=reply_text.strip(),
                reply_language=reply_language,
            )
        )
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns :]

    def clear(self) -> None:
        self._turns.clear()

    def build_capped_context(self, current_agent_span: str = "") -> Tuple[str, Dict[str, int]]:
        """Build context from at most `max_turns` and `current_agent_span` within token cap."""
        kept_turns = list(self._turns[-self.max_turns :])
        span_clean = (current_agent_span or "").strip()
        # Cap agent span to half of max_context_tokens so recent turns fit
        max_span_chars = (self.max_context_tokens // 2) * 4
        if len(span_clean) > max_span_chars:
            span_clean = span_clean[:max_span_chars].rstrip() + "..."

        while kept_turns:
            lines = [
                f"Turn {idx + 1} ({t.reply_language}): "
                f"User={t.user_transcript} | Assistant={t.reply_text}"
                for idx, t in enumerate(kept_turns)
            ]
            if span_clean:
                lines.append(f"ActiveEditorSpan: {span_clean}")
            candidate = "\n".join(lines)
            if self.estimate_tokens(candidate) <= self.max_context_tokens:
                break
            kept_turns.pop(0)

        final_lines = [
            f"Turn {idx + 1} ({t.reply_language}): "
            f"User={t.user_transcript} | Assistant={t.reply_text}"
            for idx, t in enumerate(kept_turns)
        ]
        if span_clean:
            final_lines.append(f"ActiveEditorSpan: {span_clean}")
        context_str = "\n".join(final_lines)
        tokens_used = self.estimate_tokens(context_str)
        if tokens_used > self.max_context_tokens:
            # Hard character truncation if ActiveEditorSpan alone exceeded cap
            context_str = context_str[: self.max_context_tokens * 4]
            tokens_used = min(self.max_context_tokens, self.estimate_tokens(context_str))

        cap_meta = {
            "memory_turns_kept": len(kept_turns),
            "memory_max_turns": self.max_turns,
            "memory_tokens_used": tokens_used,
            "memory_token_cap": self.max_context_tokens,
        }
        logger.info(
            "Conversation memory context built: turns_kept=%d/%d, tokens_used=%d/%d",
            cap_meta["memory_turns_kept"],
            cap_meta["memory_max_turns"],
            cap_meta["memory_tokens_used"],
            cap_meta["memory_token_cap"],
        )
        return context_str, cap_meta


class ConversationalTurnPlan(BaseModel):
    """Validated conversational turn output schema."""

    model_config = ConfigDict(extra="forbid")

    reply_text: str = Field(min_length=1, max_length=600)
    reply_language: ReplyLanguageCode
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    is_system_fallback: bool = False
    provider: str = "system_fallback"
    model: str = "system_fallback"
    llm_latency_ms: float = 0.0
    script_check_passed: bool = True
    script_check_detail: str = "ok"
    validation_failures: List[str] = Field(default_factory=list)
    memory_cap_info: Dict[str, int] = Field(default_factory=dict)


def detect_utterance_target_language(
    transcript: str,
    selected_language: str = "en-IN",
) -> ReplyLanguageCode:
    """Determine expected `reply_language` from the user's utterance script & selected language."""
    devanagari = 0
    telugu = 0
    tamil = 0
    latin = 0
    for ch in transcript or "":
        cp = ord(ch)
        if 0x0900 <= cp <= 0x097F:
            devanagari += 1
        elif 0x0C00 <= cp <= 0x0C7F:
            telugu += 1
        elif 0x0B80 <= cp <= 0x0BFF:
            tamil += 1
        elif ("a" <= ch.lower() <= "z") and ord(ch) < 128:
            latin += 1

    if devanagari > 0 and devanagari >= max(telugu, tamil):
        return "hi-IN"
    if telugu > 0 and telugu >= max(devanagari, tamil):
        return "te-IN"
    if tamil > 0 and tamil >= max(devanagari, telugu):
        return "ta-IN"

    # Utterance is in Latin script (or empty): check for Tenglish, Tanglish, or Hinglish markers
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", transcript or "")}
    sel = (selected_language or "en-IN").strip()
    if words & _TENGLISH_LATIN_MARKERS:
        return "te-IN"
    if words & _TANGLISH_LATIN_MARKERS:
        return "ta-IN"
    if sel == "hi-en" or (words & _HINGLISH_LATIN_MARKERS):
        return "hi-en"
    if sel in ("hi-IN", "te-IN", "ta-IN", "en-IN"):
        if sel == "hi-IN" and latin > 0 and devanagari == 0:
            return "hi-en"
        return sel  # type: ignore[return-value]
    return "en-IN"


def truncate_to_two_sentences(text: str) -> Tuple[str, bool]:
    """Truncate `text` to at most two short sentences; returns `(truncated_text, was_truncated)`."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return "", False
    # Split on sentence-ending punctuation (., !, ?, Devanagari danda ।) while keeping delimiters
    parts = re.split(r"(?<=[.!?।])\s+", cleaned)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 2:
        return cleaned, False
    truncated = f"{parts[0]} {parts[1]}".strip()
    return truncated, True


def convert_number_words_to_digits(text: str) -> str:
    """Convert standalone number words in `text` to digits per system prompt rule."""
    if not text:
        return text
    tokens = text.split(" ")
    out: List[str] = []
    for tok in tokens:
        stripped = tok.strip(".,!?।\"'()")
        low = stripped.lower()
        if low in _NUMBER_WORD_MAP:
            out.append(tok.replace(stripped, _NUMBER_WORD_MAP[low], 1))
        else:
            out.append(tok)
    return " ".join(out)


def check_timbre_tags(text: str) -> Tuple[bool, List[str]]:
    """Verify no `<...>` tags appear in `text` except `<pauses>`."""
    tags = re.findall(r"<[^>]+>", text or "")
    forbidden = [t for t in tags if t != "<pauses>"]
    return len(forbidden) == 0, forbidden


def verify_reply_script(reply_text: str, reply_language: str) -> Tuple[bool, str]:
    """Check that `reply_text` script matches `reply_language`.

    Rules:
    - `hi-IN`: mostly Devanagari (`>= 50%` of letter characters), Latin loanwords allowed.
    - `te-IN`: Telugu script (`> 50%` of letter characters).
    - `ta-IN`: Tamil script (`> 50%` of letter characters).
    - `en-IN` and `hi-en`: Latin script (`>= 70%` of letter characters).
    """
    if reply_language not in ALLOWED_REPLY_LANGUAGES:
        return False, f"invalid_reply_language:{reply_language}"

    # Remove <pauses> and System Message prefix before counting script letters
    text_wo_tags = (
        (reply_text or "").replace("<pauses>", " ").replace("[System Message]", " ").strip()
    )
    devanagari = 0
    telugu = 0
    tamil = 0
    latin = 0
    other_letters = 0

    for ch in text_wo_tags:
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if not (cat.startswith("L") or cat in ("Mn", "Mc")):
            continue
        if 0x0900 <= cp <= 0x097F:
            devanagari += 1
        elif 0x0C00 <= cp <= 0x0C7F:
            telugu += 1
        elif 0x0B80 <= cp <= 0x0BFF:
            tamil += 1
        elif ("a" <= ch.lower() <= "z") and cp < 128:
            latin += 1
        else:
            other_letters += 1

    total_letters = devanagari + telugu + tamil + latin + other_letters
    if total_letters == 0:
        return False, "script_check_failed:no_letters"

    if reply_language == "hi-IN":
        ratio = devanagari / float(total_letters)
        if ratio >= 0.50 and telugu == 0 and tamil == 0:
            return True, f"hi-IN_devanagari_ratio={ratio:.2f}"
        return False, f"script_check_failed:hi-IN_devanagari_ratio={ratio:.2f}_lt_0.50"

    if reply_language == "te-IN":
        ratio = telugu / float(total_letters)
        if ratio > 0.50 and devanagari == 0 and tamil == 0:
            return True, f"te-IN_telugu_ratio={ratio:.2f}"
        return False, f"script_check_failed:te-IN_telugu_ratio={ratio:.2f}_le_0.50"

    if reply_language == "ta-IN":
        ratio = tamil / float(total_letters)
        if ratio > 0.50 and devanagari == 0 and telugu == 0:
            return True, f"ta-IN_tamil_ratio={ratio:.2f}"
        return False, f"script_check_failed:ta-IN_tamil_ratio={ratio:.2f}_le_0.50"

    # en-IN or hi-en -> Latin script
    ratio = latin / float(total_letters)
    if ratio >= 0.70 and devanagari == 0 and telugu == 0 and tamil == 0:
        return True, f"{reply_language}_latin_ratio={ratio:.2f}"
    indic_cnt = devanagari + telugu + tamil
    return (
        False,
        f"script_check_failed:{reply_language}_latin_ratio={ratio:.2f}_indic={indic_cnt}",
    )


def build_conversational_system_prompt(
    selected_language: str,
    expected_reply_language: str,
    memory_context: str = "",
) -> str:
    """Build Phase 6 system prompt with explicit language, script, sentence, and tag rules."""
    ctx_block = (
        f"\nRecent Conversation Context (Capped):\n{memory_context}\n"
        if memory_context
        else ""
    )
    return (
        "You are Rahul, a witty, funny, energetic male voice assistant for WinAI-OE on Windows 11.\n"
        f"User's selected UI language setting: {selected_language}\n"
        f"Detected language/script of user's last utterance: {expected_reply_language}\n"
        f"{ctx_block}\n"
        "STRICT OUTPUT RULES:\n"
        "1. Reply in the EXACT same language and script as the user's last utterance "
        f'(`reply_language` MUST be "{expected_reply_language}").\n'
        "   - If `hi-IN`: write `reply_text` in Devanagari script (Latin loanwords allowed).\n"
        "   - If `hi-en`: Hinglish in, Hinglish out (Romanized Hindi in Latin script).\n"
        "   - If `te-IN`: write `reply_text` in Telugu script.\n"
        "   - If `ta-IN`: write `reply_text` in Tamil script.\n"
        "   - If `en-IN`: write `reply_text` in English (Latin script).\n"
        "2. Keep `reply_text` to AT MOST two short sentences with a fun, playful, witty buddy tone.\n"
        "3. Always use digits (0-9) for numbers (e.g., 2, 10), never spelled-out number words.\n"
        "4. Do NOT include any TTS/Timbre tags except `<pauses>`.\n"
        "5. Return ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "reply_text": "<1 or 2 short sentences>",\n'
        f'  "reply_language": "{expected_reply_language}",\n'
        '  "actions": [\n'
        "    {\n"
        '      "action_type": '
        '"launch_app|type_in_notepad|create_workspace_file|read_workspace_file",\n'
        '      "arguments": {},\n'
        '      "user_facing_summary": "<short summary>"\n'
        "    }\n"
        "  ]\n"
        "}"
    )


LLMCallable = Callable[[List[ChatMessage]], Coroutine[Any, Any, Tuple[str, str, str]]]


class ConversationalTurnPlanner:
    """Cancellable Phase 6 conversational planner with truncation, 1 retry, and fallback."""

    def __init__(
        self,
        llm_provider: Optional[BaseModelProvider] = None,
        custom_llm_fn: Optional[LLMCallable] = None,
        timeout_seconds: float = 8.0,
        max_memory_turns: int = 3,
        max_memory_tokens: int = 240,
    ) -> None:
        self.llm_provider = llm_provider
        self.custom_llm_fn = custom_llm_fn
        self.timeout_seconds = max(0.2, float(timeout_seconds))
        self.memory = ConversationalMemoryBuffer(
            max_turns=max_memory_turns,
            max_context_tokens=max_memory_tokens,
        )
        self._active_llm_task: Optional[asyncio.Task[Any]] = None

    def cancel_active_turn(self) -> bool:
        """Cancel any in-flight LLM request immediately on stop/undo barge-in."""
        if self._active_llm_task is not None and not self._active_llm_task.done():
            self._active_llm_task.cancel()
            logger.info("Cancelled in-flight conversational LLM request due to barge-in.")
            return True
        return False

    async def _invoke_llm_once(
        self, messages: List[ChatMessage]
    ) -> Tuple[str, str, str]:
        """Invoke either `custom_llm_fn` or `llm_provider.complete` inside a cancellable task."""
        if self.custom_llm_fn is not None:
            coro = self.custom_llm_fn(messages)
            self._active_llm_task = asyncio.create_task(coro)
            try:
                return await asyncio.wait_for(self._active_llm_task, timeout=self.timeout_seconds)
            finally:
                self._active_llm_task = None

        if self.llm_provider is None:
            raise RuntimeError("No conversational LLM provider configured.")

        caps = self.llm_provider.get_capabilities()
        provider_name = caps.provider_name
        model_name = (
            getattr(self.llm_provider, "model_name", None)
            or getattr(caps, "model_name", None)
            or "unknown"
        )

        async def _call() -> Tuple[str, str, str]:
            resp = await self.llm_provider.complete(
                messages=messages,
                temperature=0.2,
                max_tokens=260,
            )
            content = getattr(resp, "content", "") if not isinstance(resp, str) else resp
            return str(content or ""), provider_name, str(model_name)

        self._active_llm_task = asyncio.create_task(_call())
        try:
            return await asyncio.wait_for(self._active_llm_task, timeout=self.timeout_seconds)
        finally:
            self._active_llm_task = None

    @staticmethod
    def _extract_json_dict(
        raw_text: str, default_language: str = "en-IN"
    ) -> Dict[str, Any]:
        cleaned = (raw_text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        # If the LLM replied directly in natural conversational sentences without JSON braces,
        # wrap it into the candidate envelope so _validate_candidate_dict checks script & length!
        if cleaned and "{" not in cleaned and "}" not in cleaned:
            return {
                "reply_text": cleaned,
                "reply_language": default_language,
                "actions": [],
            }
        raise ValueError("LLM response is not a valid JSON object or plain-text reply.")

    def _validate_candidate_dict(
        self,
        data: Dict[str, Any],
        expected_reply_language: ReplyLanguageCode,
        attempt_idx: int,
        validation_failures: List[str],
    ) -> Optional[ Tuple[str, ReplyLanguageCode, List[Dict[str, Any]], str] ]:
        """Validate candidate JSON dict; truncate to 2 sentences first; log any rule violation."""
        raw_reply = str(data.get("reply_text") or "").strip()
        if not raw_reply:
            rule = f"attempt_{attempt_idx}:empty_reply_text"
            validation_failures.append(rule)
            logger.warning("Conversational validation failure: %s", rule)
            return None

        raw_lang = str(data.get("reply_language") or "").strip()
        if raw_lang not in ALLOWED_REPLY_LANGUAGES:
            rule = f"attempt_{attempt_idx}:invalid_reply_language:{raw_lang}"
            validation_failures.append(rule)
            logger.warning("Conversational validation failure: %s", rule)
            return None

        # Step 1: Truncate to at most 2 sentences (do not fall back on >2 sentences)
        truncated_reply, was_truncated = truncate_to_two_sentences(raw_reply)
        if was_truncated:
            rule = f"attempt_{attempt_idx}:truncated_to_two_sentences"
            validation_failures.append(rule)
            logger.info("Truncated LLM reply to 2 sentences (%s).", rule)

        # Step 2: Convert number words to digits
        normalized_reply = convert_number_words_to_digits(truncated_reply)

        # Step 3: Check Timbre tags (only <pauses> allowed)
        tags_ok, forbidden_tags = check_timbre_tags(normalized_reply)
        if not tags_ok:
            rule = f"attempt_{attempt_idx}:forbidden_timbre_tags:{','.join(forbidden_tags)}"
            validation_failures.append(rule)
            logger.warning("Conversational validation failure: %s", rule)
            return None

        # Step 4: Check language alignment with expected_reply_language
        if raw_lang != expected_reply_language:
            rule = (
                f"attempt_{attempt_idx}:language_mismatch:"
                f"got_{raw_lang}_expected_{expected_reply_language}"
            )
            validation_failures.append(rule)
            logger.warning("Conversational validation failure: %s", rule)
            return None

        # Step 5: Per-reply_language script check
        script_ok, script_detail = verify_reply_script(normalized_reply, raw_lang)
        if not script_ok:
            rule = f"attempt_{attempt_idx}:{script_detail}"
            validation_failures.append(rule)
            logger.warning("Conversational validation failure: %s", rule)
            return None

        raw_actions = data.get("actions")
        if raw_actions is None:
            actions_list: List[Dict[str, Any]] = []
        elif isinstance(raw_actions, list):
            actions_list = [a for a in raw_actions if isinstance(a, dict)]
        else:
            rule = f"attempt_{attempt_idx}:actions_not_a_list"
            validation_failures.append(rule)
            logger.warning("Conversational validation failure: %s", rule)
            return None

        return normalized_reply, raw_lang, actions_list, script_detail  # type: ignore[return-value]

    async def plan_turn(
        self,
        transcript: str,
        *,
        selected_language: str = "en-IN",
        current_agent_span: str = "",
    ) -> ConversationalTurnPlan:
        """Plan a conversational turn with truncation, 1 retry, and [System Message] fallback."""
        # Fast-path for 'Rahul' / 'Rahul em chesthunav' wake-word greetings (<1ms response)
        instant = match_instant_rahul_greeting(transcript, selected_language)
        if instant is not None and self.custom_llm_fn is None:
            reply_txt, reply_lang = instant
            _, cap_meta = self.memory.build_capped_context(current_agent_span)
            self.memory.add_turn(transcript, reply_txt, reply_lang)
            script_ok, script_detail = verify_reply_script(reply_txt, reply_lang)
            return ConversationalTurnPlan(
                reply_text=reply_txt,
                reply_language=reply_lang,
                actions=[],
                is_system_fallback=False,
                provider="rahul_instant",
                model="wake_greeting_v1",
                llm_latency_ms=0.1,
                script_check_passed=script_ok,
                script_check_detail=script_detail,
                validation_failures=[],
                memory_cap_info=cap_meta,
            )

        expected_lang = detect_utterance_target_language(transcript, selected_language)
        memory_ctx, cap_meta = self.memory.build_capped_context(current_agent_span)
        sys_prompt = build_conversational_system_prompt(
            selected_language=selected_language,
            expected_reply_language=expected_lang,
            memory_context=memory_ctx,
        )

        validation_failures: List[str] = []
        messages: List[ChatMessage] = [
            ChatMessage(role="system", content=sys_prompt),
            ChatMessage(role="user", content=transcript.strip()),
        ]

        t0 = time.perf_counter()
        last_provider = "system_fallback"
        last_model = "system_fallback"

        for attempt_idx in (1, 2):
            try:
                raw_text, prov_name, mod_name = await self._invoke_llm_once(messages)
                last_provider = prov_name
                last_model = mod_name
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                rule = f"attempt_{attempt_idx}:llm_timeout_after_{self.timeout_seconds}s"
                validation_failures.append(rule)
                logger.warning("Conversational LLM timed out: %s", rule)
                break
            except Exception as exc:
                rule = f"attempt_{attempt_idx}:llm_error:{type(exc).__name__}"
                validation_failures.append(rule)
                logger.warning("Conversational LLM error: %s (%s)", rule, exc)
                break

            try:
                parsed_dict = self._extract_json_dict(raw_text, default_language=expected_lang)
            except Exception:
                rule = f"attempt_{attempt_idx}:invalid_json"
                validation_failures.append(rule)
                logger.warning("Conversational validation failure: %s", rule)
                parsed_dict = None

            if parsed_dict is not None:
                validated = self._validate_candidate_dict(
                    parsed_dict,
                    expected_reply_language=expected_lang,
                    attempt_idx=attempt_idx,
                    validation_failures=validation_failures,
                )
                if validated is not None:
                    reply_text, reply_lang, actions_list, script_detail = validated
                    llm_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                    self.memory.add_turn(transcript, reply_text, reply_lang)
                    return ConversationalTurnPlan(
                        reply_text=reply_text,
                        reply_language=reply_lang,
                        actions=actions_list,
                        is_system_fallback=False,
                        provider=last_provider,
                        model=last_model,
                        llm_latency_ms=llm_ms,
                        script_check_passed=True,
                        script_check_detail=script_detail,
                        validation_failures=validation_failures,
                        memory_cap_info=cap_meta,
                    )

            # Retry once with explicit corrective feedback
            if attempt_idx == 1:
                last_failure = (
                    validation_failures[-1] if validation_failures else "validation_failed"
                )
                messages.append(ChatMessage(role="assistant", content=raw_text or "{}"))
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            f"Your previous response failed validation rule `{last_failure}`. "
                            f"Respond with valid JSON where `reply_language` is `{expected_lang}`, "
                            f"`reply_text` is in the matching script for `{expected_lang}` "
                            "(max 2 short sentences, digits for numbers, no tags except <pauses>)."
                        ),
                    )
                )

        # Fallback to hardcoded [System Message] in expected_lang
        llm_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        fallback_line = SYSTEM_FALLBACK_MESSAGES.get(
            expected_lang, SYSTEM_FALLBACK_MESSAGES["en-IN"]
        )
        script_ok, script_detail = verify_reply_script(fallback_line, expected_lang)
        return ConversationalTurnPlan(
            reply_text=fallback_line,
            reply_language=expected_lang,
            actions=[],
            is_system_fallback=True,
            provider=last_provider,
            model=last_model,
            llm_latency_ms=llm_ms,
            script_check_passed=script_ok,
            script_check_detail=f"system_fallback:{script_detail}",
            validation_failures=validation_failures,
            memory_cap_info=cap_meta,
        )


async def execute_conversational_actions(
    coordinator: Any,
    session: Any,
    actions: Sequence[Dict[str, Any]],
    *,
    speak_response: bool = False,
) -> List[Any]:
    """Route LLM actions via `VoiceIntentPlanner`, `SecurityPolicyEngine`, & `ApprovalBroker`."""
    results: List[Any] = []
    orig_planner_fn = coordinator.planner.planner_fn
    try:
        for idx, action_dict in enumerate(actions):
            if not isinstance(action_dict, dict):
                continue
            envelope_dict = {k: v for k, v in action_dict.items() if k != "target_resource"}

            async def _return_candidate(
                _transcript: str, candidate: Dict[str, Any] = envelope_dict
            ) -> Dict[str, Any]:
                return candidate

            coordinator.planner.planner_fn = _return_candidate
            summary_txt = str(
                action_dict.get("user_facing_summary")
                or action_dict.get("action_type")
                or f"action_{idx + 1}"
            )
            res = await coordinator.process_transcript(
                session=session,
                transcript=summary_txt,
                speak_response=speak_response,
            )
            results.append(res)
    finally:
        coordinator.planner.planner_fn = orig_planner_fn
    return results


async def _run_wav_driven_80_turns_cli() -> int:
    """Run 20 WAV-driven turns per language (hi, te, ta, en = 80 turns) via STT -> LLM -> TTS."""
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    from src.providers.gemini_adapter import GeminiAdapter
    from src.storage.credential_vault import CredentialVault
    from src.voice.base import GnaniConfig, TTSAudioConfig, TTSRequest
    from src.voice.gnani_stt import GnaniSTTProvider
    from src.voice.gnani_tts import GnaniTTSProvider

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    repo_root = Path(__file__).resolve().parents[2]
    eval_dir = Path(r"C:\Users\RAHUL\meo\data\eval")
    if not eval_dir.exists():
        eval_dir = repo_root / "data" / "eval"

    vault = CredentialVault()
    gnani_cfg = GnaniConfig.from_env_or_vault(vault=vault, allow_vault_lookup=True)
    stt = GnaniSTTProvider(config=gnani_cfg)
    tts = GnaniTTSProvider(
        config=gnani_cfg,
        cache_dir=repo_root / ".winai" / "tts_cache",
    )
    gemini_key = vault.get_credential("gemini") or ""

    # Recorded non-stop command/conversational WAVs per language from data/eval
    wavs_by_lang = {
        "hi": [
            ("08_hi_command.wav", "hi-IN"),
            ("99_hi_command_noisy.wav", "hi-IN"),
            ("06_hi_correct.wav", "hi-IN"),
            ("07_hi_correct.wav", "hi-IN"),
            ("70_hi_none.wav", "hi-IN"),
            ("71_hi_none.wav", "hi-IN"),
            ("72_hi_none.wav", "hi-IN"),
            ("73_hi_none.wav", "hi-IN"),
        ],
        "te": [
            ("16_te_command.wav", "te-IN"),
            ("100_te_command_noisy.wav", "te-IN"),
            ("14_te_correct.wav", "te-IN"),
            ("15_te_correct.wav", "te-IN"),
            ("74_te_none.wav", "te-IN"),
            ("75_te_none.wav", "te-IN"),
        ],
        "ta": [
            ("24_ta_command.wav", "ta-IN"),
            ("22_ta_correct.wav", "ta-IN"),
            ("23_ta_correct.wav", "ta-IN"),
            ("76_ta_none.wav", "ta-IN"),
            ("77_ta_none.wav", "ta-IN"),
        ],
        "en": [
            ("69_en_command.wav", "en-IN"),
            ("67_en_correct.wav", "en-IN"),
            ("68_en_correct.wav", "en-IN"),
            ("98_en_correct_noisy.wav", "en-IN"),
            ("84_en_none.wav", "en-IN"),
            ("85_en_none.wav", "en-IN"),
        ],
    }

    stt_cache: Dict[str, Tuple[str, float]] = {}
    gnani_lock = asyncio.Lock()

    async def _run_lang_track(lang_short: str) -> List[Dict[str, Any]]:
        llm = GeminiAdapter(api_key=gemini_key, model_name="gemini-2.5-flash-lite", timeout=12.0)
        planner = ConversationalTurnPlanner(
            llm_provider=llm,
            timeout_seconds=12.0,
            max_memory_turns=3,
            max_memory_tokens=240,
        )
        pool = wavs_by_lang[lang_short]
        track_rows: List[Dict[str, Any]] = []
        for turn_idx in range(1, 21):
            wav_name, lang_code = pool[(turn_idx - 1) % len(pool)]
            wav_path = eval_dir / wav_name

            async with gnani_lock:
                if wav_name not in stt_cache:
                    t_stt0 = time.perf_counter()
                    stt_res = await stt.transcribe_bytes(
                        wav_path.read_bytes(),
                        language_code=lang_code,
                        filename=wav_name,
                    )
                    stt_ms = round((time.perf_counter() - t_stt0) * 1000.0, 2)
                    stt_cache[wav_name] = (stt_res.transcript.strip(), stt_ms)
                    await asyncio.sleep(0.15)
                transcript_text, stt_ms = stt_cache[wav_name]

            plan = await planner.plan_turn(
                transcript_text,
                selected_language=lang_code,
                current_agent_span=f"Turn {turn_idx} active Notepad buffer ({lang_short})",
            )

            # Synthesize via Timbre v3.0 SSE with explicit reply_language (never auto)
            tts_req = TTSRequest(
                text=" ".join(plan.reply_text.splitlines())[:220],
                voice="Shivani",
                model="timbre-v3.0",
                language=plan.reply_language,
                audio_config=TTSAudioConfig(sample_rate=24000, container="wav"),
            )
            tts_mode = "buffered_sse_stream"
            async with gnani_lock:
                t_tts0 = time.perf_counter()
                try:
                    tts_res = await tts.synthesize_buffered_sse(tts_req, use_cache=True)
                    if getattr(tts_res, "chunks_count", 1) == 0:
                        tts_mode = "buffered_sse_cache_hit"
                    else:
                        await asyncio.sleep(0.12)
                except Exception as exc:
                    tts_mode = f"error:{type(exc).__name__}"
                tts_ms = round((time.perf_counter() - t_tts0) * 1000.0, 2)
            total_ms = round(stt_ms + plan.llm_latency_ms + tts_ms, 2)

            row_rec = {
                "mode": "WAV-driven",
                "language": lang_short,
                "turn": turn_idx,
                "wav_file": wav_name,
                "transcript": transcript_text,
                "reply_text": plan.reply_text,
                "reply_language": plan.reply_language,
                "provider": plan.provider,
                "model": plan.model,
                "is_system_fallback": plan.is_system_fallback,
                "script_check_passed": plan.script_check_passed,
                "script_check_detail": plan.script_check_detail,
                "validation_failures": plan.validation_failures,
                "memory_cap_info": plan.memory_cap_info,
                "stt_latency_ms": stt_ms,
                "llm_latency_ms": plan.llm_latency_ms,
                "tts_first_audio_ms": tts_ms,
                "tts_synthesis_mode": tts_mode,
                "total_turn_ms": total_ms,
            }
            track_rows.append(row_rec)
            print(
                f"[{lang_short} {turn_idx:02d}/20] {wav_name} -> "
                f"lang={plan.reply_language} script_ok={plan.script_check_passed} "
                f"llm={plan.llm_latency_ms}ms tts={tts_ms}ms ({tts_mode}) "
                f"reply={plan.reply_text!r}",
                flush=True,
            )
        return track_rows

    track_lists = await asyncio.gather(
        _run_lang_track("hi"),
        _run_lang_track("te"),
        _run_lang_track("ta"),
        _run_lang_track("en"),
    )
    all_rows: List[Dict[str, Any]] = []
    for tl in track_lists:
        all_rows.extend(tl)

    out_dir = repo_root / "eval_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_json = out_dir / f"phase6_wav_driven_80_turns_{ts}.json"
    out_json.write_text(
        json.dumps(
            {
                "mode": (
                    "WAV-driven (recorded command WAVs -> Gnani STT -> "
                    "Gemini LLM -> Gnani Timbre v3.0 SSE TTS)"
                ),
                "total_turns": len(all_rows),
                "rows": all_rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Saved 80 WAV-driven conversational turns to: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_wav_driven_80_turns_cli()))


