"""Strict structured intent schema, 5-action allowlist, and output validator (Milestone 3).

Security Guarantees:
- Treats all transcripts and LLM/planner outputs as untrusted inputs.
- Strips and ignores any model-supplied approval or risk fields (`user_approved`,
  `approved`, `approval_nonce`, `bypass_policy`, `risk_category`, `requires_confirmation`).
- Enforces `extra="forbid"` on both outer intent envelopes and per-action argument schemas.
- Restricts voice execution to the 5 allowlisted actions (`system_info`,
  `list_approved_apps`, `launch_app`, `read_workspace_file`, `create_workspace_file`).
- Computes `risk_category` and `requires_confirmation` exclusively in trusted host code.
"""

import asyncio
import json
import logging
import re
import secrets
import unicodedata
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.orchestrator.errors import ToolValidationError
from src.providers.base import BaseModelProvider, ChatMessage
from src.security.dynamic_defense import DynamicDefenseGuard
from src.security.policy_engine import RiskTier
from src.voice.base import sanitize_error_message

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_CHARS = 2000
MAX_FILE_CONTENT_CHARS = 65536
MAX_PATH_CHARS = 255
MAX_APP_ID_CHARS = 32

_APP_ID_PATTERN = re.compile(r"^[a-z0-9_.-]{1,32}$")
_SAFE_REL_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9_./ -]{1,255}$")

# Fields that an untrusted LLM might emit to spoof authorization or risk level;
# these are stripped before strict validation and never trusted.
UNTRUSTED_MODEL_AUTH_FIELDS: Set[str] = {
    "user_approved",
    "approved",
    "is_approved",
    "approval_nonce",
    "approval_status",
    "bypass_policy",
    "force_allow",
    "risk_category",
    "requires_confirmation",
}

FORBIDDEN_EXTERNAL_FOLDER_KEYWORDS: Set[str] = {
    "desktop",
    "documents",
    "downloads",
    "appdata",
    "system32",
    "program files",
}


class AllowedVoiceAction(str, Enum):
    """Initial Milestone 3/4 allowlist of deterministic, low-risk voice actions."""

    SYSTEM_INFO = "system_info"
    LIST_APPROVED_APPS = "list_approved_apps"
    LAUNCH_APP = "launch_app"
    TYPE_IN_NOTEPAD = "type_in_notepad"
    READ_WORKSPACE_FILE = "read_workspace_file"
    CREATE_WORKSPACE_FILE = "create_workspace_file"


ACTION_TO_ENGINE_TOOL: Dict[AllowedVoiceAction, str] = {
    AllowedVoiceAction.SYSTEM_INFO: "system_get_overview",
    AllowedVoiceAction.LIST_APPROVED_APPS: "app_list_approved",
    AllowedVoiceAction.LAUNCH_APP: "app_launch",
    AllowedVoiceAction.TYPE_IN_NOTEPAD: "window_type_text",
    AllowedVoiceAction.READ_WORKSPACE_FILE: "fs_read_file",
    AllowedVoiceAction.CREATE_WORKSPACE_FILE: "fs_write_file",
}


class IntentValidationError(ToolValidationError):
    """Raised when a transcript or planner intent fails validation or is unsupported."""

    def __init__(self, code: str, message: str, remediation: str = ""):
        super().__init__(
            code=code,
            message=sanitize_error_message(message),
            remediation=sanitize_error_message(remediation),
        )


# ---------------------------------------------------------------------------
# Strict Per-Action Argument Schemas (`extra="forbid"`)
# ---------------------------------------------------------------------------


class SystemInfoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detailed: bool = False


class ListApprovedAppsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LaunchAppArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(min_length=1, max_length=MAX_APP_ID_CHARS)


class ReadWorkspaceFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    max_bytes: int = Field(default=65536, ge=1, le=1048576)


class CreateWorkspaceFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    content: str = Field(default="", max_length=MAX_FILE_CONTENT_CHARS)


class TypeInNotepadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_FILE_CONTENT_CHARS)
    save_as: Optional[str] = Field(default=None, max_length=MAX_PATH_CHARS)
    replace_last_span: bool = False


class _UntrustedIntentEnvelope(BaseModel):
    """Strict input envelope parsed from untrusted planner output."""

    model_config = ConfigDict(extra="forbid")

    intent_id: Optional[str] = Field(default=None, max_length=64)
    action_type: str = Field(min_length=1, max_length=64)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    user_facing_summary: str = Field(default="", max_length=500)
    reply_language: str = Field(default="en-IN", max_length=16)


class VoiceIntent(BaseModel):
    """Validated, immutable voice intent with host-computed risk and confirmation requirements."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: str
    action_type: AllowedVoiceAction
    engine_tool_name: str
    arguments: Dict[str, Any]
    target_resource: str
    user_facing_summary: str
    risk_category: RiskTier
    requires_confirmation: bool
    reply_language: str = "en-IN"


# ---------------------------------------------------------------------------
# Phase 3: Stop / Undo / Correction Fast-Path Lexicon & Normalizer
# ---------------------------------------------------------------------------


class FastPathIntentKind(str, Enum):
    STOP = "stop"
    UNDO = "undo"
    CORRECT = "correct"
    NONE = "none"


class FastPathMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FastPathIntentKind
    matched_entry: Optional[str] = None
    matched_rule: Optional[str] = None
    gate_decision: str = "no_match"
    normalized_transcript: str = ""


# Frozen Lexicon Scope: ONLY Hindi (hi), Telugu (te), Tamil (ta), and English (en).
# General Linguistic Rule 1 (Compound Noun Masking):
# In English and Indic code-mixing, "bus stop" (transit stop), "full stop" (period punctuation),
# and "non stop" (continuous adjective/adverb) are lexicalized compound nouns/modifiers where
# "stop" is a noun head rather than an imperative verb.
COMPOUND_NOUN_STOP_MASKS: Tuple[str, ...] = (
    "bus stop",
    "full stop",
    "non stop",
    "nonstop",
    "बस स्टॉप",
    "फुल स्टॉप",
    "नॉन स्टॉप",
    "బస్ స్టాప్",
    "ఫుల్ స్టాప్",
    "నాన్ స్టాప్",
    "பஸ் ஸ்டாப்",
    "புல் ஸ்டாப்",
)

# General Linguistic Rule 2 (Meta-linguistic definition queries):
# Utterances asking "X ka matlab kya hai" / "meaning of X" are mentions, not commands.
_METALINGUISTIC_MENTION_RE = re.compile(
    r"\b(?:ka\s+matlab|meaning\s+of|definition\s+of|अर्थ\s+क्या|मतलब\s+क्या)\b",
    re.IGNORECASE,
)

STOP_PHRASES: Tuple[str, ...] = (
    # English (en)
    "hold on",
    # Hindi (hi)
    "band karo",
    "bas band karo",
    "bas karo",
    "ek second rukiye",
    "एक सेकंड रुकिए",
    "रुकिए",
    "रुको रुको",
    "बस बंद करो",
    "बंद करो",
    "बस करो",
    # Telugu (te)
    "aagu cheyyaku",
    "oka nimisham aagandi",
    "oka nimisham aagu",
    "ఆగు చెయ్యకు",
    "ఆగండి",
    "ఆగు ఆపు",
    "నిమిషం ఆగు",
    # Tamil (ta)
    "oru nimisham nillungal",
    "oru nimisham nillu",
    "நில்லுங்கள்",
    "நில்லு நிறுத்து",
    "நிறுத்து",
)

STOP_SINGLE_TOKENS: Tuple[str, ...] = (
    # Romanized (hi / te / ta / en)
    "ruko",
    "rukko",
    "rukiye",
    "roko",
    "rokiye",
    "aapo",
    "aapu",
    "aagu",
    "aagandi",
    "nillu",
    "nillungal",
    "niruthu",
    "stop",
    "wait",
    "pause",
    "halt",
    # Native script (hi / te / ta)
    "रुको",
    "रुकिए",
    "रोको",
    "रोकिए",
    "ठहरो",
    "ఆగు",
    "ఆపు",
    "ఆగండి",
    "நில்லு",
    "நிறுத்து",
    "நில்லுங்கள்",
)

ROMANIZED_STOP_CANONICAL: Tuple[str, ...] = (
    "ruko",
    "rukko",
    "rukiye",
    "roko",
    "aapo",
    "aapu",
    "aagu",
    "aagandi",
    "nillu",
    "niruthu",
    "stop",
    "wait",
)

UNDO_PHRASES: Tuple[str, ...] = (
    # English (en)
    "undo the previous action",
    "undo that",
    # Hindi (hi)
    "pichla badlaav wapas",
    "wapas lijiye",
    "wapas karo",
    "pichla hatao",
    "पिछला बदलाव वापस",
    "वापस लीजिए",
    "वापस करो",
    "पिछला हटाओ",
    # Telugu (te)
    "venakki teesukondi",
    "teeseyyi venakki",
    "adi teeseyyi",
    "వెనక్కి తీసుకోండి",
    "తీసేయి వెనక్కి",
    "అది తీసేయి",
    "వెనక్కి",
    # Tamil (ta)
    "thiruppi eduthukonga",
    "atha azhichidu",
    "azhichidu venam",
    "திருப்பி எடுத்துக்கோங்க",
    "அதை திருப்பி",
    "அழிச்சிடு",
    "அழிச்சுடு",
    "அதை அழிச்சுடு",
)

UNDO_SINGLE_TOKENS: Tuple[str, ...] = (
    # Romanized (hi / te / ta / en)
    "undo",
    "wapas",
    "vapas",
    "cancel",
    "revert",
    "venakki",
    "teeseyyi",
    "thiruppi",
    "azhichidu",
    # Native script (hi / te / ta)
    "वापस",
    "हटाओ",
    "अंडू",
    "వెనక్కి",
    "తీసేయి",
    "திருப்பி",
    "அழிச்சிடு",
    "அழிச்சுடு",
)

ROMANIZED_UNDO_CANONICAL: Tuple[str, ...] = (
    "undo",
    "wapas",
    "vapas",
    "cancel",
    "venakki",
    "thiruppi",
    "azhichidu",
)

CORRECTION_PHRASES: Tuple[str, ...] = (
    # English (en)
    "make it clearer",
    "make the font bigger",
    "change that line",
    # Hindi (hi)
    "thoda saaf karo",
    "thoda saaf kijiye",
    "thoda saaf likho",
    "saaf karo",
    "saaf kijiye",
    "saaf likho",
    "font thoda bada",
    "थोड़ा साफ़ करो",
    "थोड़ा साफ करो",
    "साफ़ कीजिए",
    "साफ कीजिए",
    "साफ़ लिखो",
    "साफ लिखो",
    "फॉन्ट थोड़ा बड़ा",
    # Telugu (te)
    "inka clear ga cheyyi",
    "inka clear ga cheyyandi",
    "clear ga cheyyandi",
    "clear ga cheyyi",
    "line marchu",
    "font peddaga cheyyi",
    "ఇంకా క్లియర్",
    "క్లియర్ గా చేయండి",
    "క్లియర్ గా చెయ్యి",
    "ఫాంట్ పెద్దగా",
    "లైన్ మార్చు",
    # Tamil (ta)
    "innum thelivaa maatrunga",
    "innum thelivaa panni kudu",
    "innum thelivaa",
    "இன்னும் தெளிவா",
    "தெளிவாக மாற்றுங்கள்",
    "தெளிவா பண்ணி",
)


def _collapse_repeated_ascii_letters(word: str) -> str:
    return re.sub(r"([a-z])\1+", r"\1", word)


_COLLAPSED_STOP_SINGLE_TOKENS: Set[str] = {
    _collapse_repeated_ascii_letters(t) for t in STOP_SINGLE_TOKENS
}
_COLLAPSED_UNDO_SINGLE_TOKENS: Set[str] = {
    _collapse_repeated_ascii_letters(t) for t in UNDO_SINGLE_TOKENS
}


def _edit_distance_le_1(a: str, b: str) -> bool:
    """Return True if Levenshtein edit distance between `a` and `b` is <= 1."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = sum(1 for ca, cb in zip(a, b, strict=True) if ca != cb)
        return diffs <= 1
    # Insertion / deletion of 1 character
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    i = j = diffs = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            diffs += 1
            if diffs > 1:
                return False
            j += 1
    return True


def normalize_utterance_for_lexicon(text: str) -> Tuple[str, List[str], List[str]]:
    """Normalize transcript: Unicode NFC, lowercase, strip punctuation, and collapse repeats.

    Returns `(normalized_string, raw_tokens, collapsed_tokens)`.
    """
    if not isinstance(text, str):
        return "", [], []
    nfc = unicodedata.normalize("NFC", text).lower()
    # Keep Unicode letters (L*), combining marks (Mn/Mc), digits (Nd), and spaces
    chars: List[str] = []
    for ch in nfc:
        cat = unicodedata.category(ch)
        if cat.startswith("L") or cat in ("Mn", "Mc", "Nd"):
            chars.append(ch)
        else:
            chars.append(" ")
    cleaned = re.sub(r"\s+", " ", "".join(chars)).strip()
    raw_tokens = [t for t in cleaned.split(" ") if t]
    collapsed_tokens = [_collapse_repeated_ascii_letters(t) for t in raw_tokens]
    return cleaned, raw_tokens, collapsed_tokens


def match_fast_path_lexicon(
    transcript: str,
    current_state: Optional[str] = None,
    max_tokens: int = 6,
) -> FastPathMatch:
    """Evaluate a raw transcript segment against the Stop / Undo / Correction lexicon.

    Rules:
    1. Exact token match only for English tokens (`stop`, `wait`, `pause`, `halt`,
       `undo`, `cancel`, `revert`).
    2. Edit distance 1 only for romanized Indic tokens (`hi`/`te`/`ta`) of length >= 5
       after normalization.
    3. State gate: Stop/Undo fast-path acts only when `current_state` is `ACTING`,
       `SPEAKING`, or `CORRECTING` (when `current_state` is supplied; in `LISTENING`
       state it is gated so the utterance is treated as a normal command).
    4. Length/Boundary gate: requires `len(raw_tokens) <= max_tokens` (default 6) OR
       the stop/undo word at the start or end of the utterance.
    5. Logs every match rule and gate decision.
    """
    cleaned, raw_tokens, collapsed_tokens = normalize_utterance_for_lexicon(transcript)
    if not cleaned:
        logger.info("FastPath gate_decision=empty_transcript")
        return FastPathMatch(
            kind=FastPathIntentKind.NONE,
            gate_decision="empty_transcript",
            normalized_transcript="",
        )

    # 1. General linguistic check: metalinguistic definition queries ("X ka matlab kya hai")
    if _METALINGUISTIC_MENTION_RE.search(cleaned):
        logger.info("FastPath gate_decision=gated_metalinguistic transcript=%r", cleaned)
        return FastPathMatch(
            kind=FastPathIntentKind.NONE,
            gate_decision="gated_metalinguistic",
            normalized_transcript=cleaned,
        )

    # 2. Check Correction phrases first (before single-word tokens)
    for phrase in CORRECTION_PHRASES:
        norm_p, _, _ = normalize_utterance_for_lexicon(phrase)
        if norm_p and norm_p in cleaned:
            logger.info(
                "FastPath matched kind=correct entry=%r rule=exact_phrase_match "
                "gate=allowed_correction",
                phrase,
            )
            return FastPathMatch(
                kind=FastPathIntentKind.CORRECT,
                matched_entry=phrase,
                matched_rule="exact_phrase_match",
                gate_decision="allowed_correction",
                normalized_transcript=cleaned,
            )

    # Direct prohibitory verb modifiers ("don't stop" -> ["don", "t", "stop"], "mat ruko")
    # Note: interjections "no", "nahi", "नहीं" are NOT prohibitory verb modifiers ("no, stop"
    # and "nahi, ruko" mean "no [wait], stop!" and must halt in ACTING state); "na" is removed.
    negation_particles = {"t", "dont", "not", "never", "mat", "मत"}
    english_exact_only = {"stop", "wait", "pause", "halt", "undo", "cancel", "revert"}

    candidate_kind = FastPathIntentKind.NONE
    candidate_entry: Optional[str] = None
    candidate_rule: Optional[str] = None
    at_boundary = False
    non_boundary_fallback: Optional[Tuple[FastPathIntentKind, str, str]] = None

    def _record_candidate(
        k: FastPathIntentKind,
        entry: str,
        rule: str,
        is_edge: bool,
    ) -> bool:
        nonlocal candidate_kind, candidate_entry, candidate_rule, at_boundary, non_boundary_fallback
        if len(raw_tokens) <= max_tokens or is_edge:
            candidate_kind = k
            candidate_entry = entry
            candidate_rule = rule
            at_boundary = is_edge
            return True
        if non_boundary_fallback is None:
            non_boundary_fallback = (k, entry, rule)
        return False

    # 3. Check Undo phrases
    for phrase in UNDO_PHRASES:
        norm_p, _, _ = normalize_utterance_for_lexicon(phrase)
        if norm_p and f" {norm_p} " in f" {cleaned} ":
            edge = cleaned.startswith(norm_p) or cleaned.endswith(norm_p)
            if _record_candidate(FastPathIntentKind.UNDO, phrase, "exact_phrase_match", edge):
                break

    # 4. Mask general compound noun phrases ("bus stop", "full stop", "non stop")
    masked = f" {cleaned} "
    for fp in COMPOUND_NOUN_STOP_MASKS:
        norm_fp, _, _ = normalize_utterance_for_lexicon(fp)
        if norm_fp:
            masked = masked.replace(f" {norm_fp} ", " ")
    masked_clean = re.sub(r"\s+", " ", masked).strip()
    if not masked_clean and candidate_kind == FastPathIntentKind.NONE:
        logger.info("FastPath gate_decision=gated_compound_noun transcript=%r", cleaned)
        return FastPathMatch(
            kind=FastPathIntentKind.NONE,
            gate_decision="gated_compound_noun",
            normalized_transcript=cleaned,
        )

    masked_tokens = [t for t in masked_clean.split(" ") if t]
    masked_collapsed = [_collapse_repeated_ascii_letters(t) for t in masked_tokens]

    # 5. Check Stop phrases on masked string
    if candidate_kind == FastPathIntentKind.NONE:
        for phrase in STOP_PHRASES:
            norm_p, _, _ = normalize_utterance_for_lexicon(phrase)
            if norm_p and f" {norm_p} " in f" {masked_clean} ":
                prefix_part = masked_clean.split(norm_p, 1)[0].strip().split()
                if prefix_part and prefix_part[-1] in negation_particles:
                    logger.info(
                        "FastPath gate_decision=gated_negated_imperative phrase=%r transcript=%r",
                        phrase,
                        cleaned,
                    )
                    return FastPathMatch(
                        kind=FastPathIntentKind.NONE,
                        matched_entry=phrase,
                        gate_decision="gated_negated_imperative",
                        normalized_transcript=cleaned,
                    )
                edge = masked_clean.startswith(norm_p) or masked_clean.endswith(norm_p)
                if _record_candidate(FastPathIntentKind.STOP, phrase, "exact_phrase_match", edge):
                    break

    # 6. Check single-token Undo matches
    if candidate_kind == FastPathIntentKind.NONE:
        for idx, (tok, ctok) in enumerate(zip(masked_tokens, masked_collapsed, strict=True)):
            prev_neg = idx > 0 and masked_tokens[idx - 1] in negation_particles
            is_edge = idx == 0 or idx == len(masked_tokens) - 1
            if tok in english_exact_only:
                if tok in UNDO_SINGLE_TOKENS:
                    if prev_neg:
                        logger.info("FastPath gate_decision=gated_negated_imperative tok=%r", tok)
                        return FastPathMatch(
                            kind=FastPathIntentKind.NONE,
                            matched_entry=tok,
                            gate_decision="gated_negated_imperative",
                            normalized_transcript=cleaned,
                        )
                    if _record_candidate(
                        FastPathIntentKind.UNDO,
                        tok,
                        "exact_english_token_match",
                        is_edge,
                    ):
                        break
                continue

            if (
                tok in UNDO_SINGLE_TOKENS
                or (not tok.isascii() and ctok in _COLLAPSED_UNDO_SINGLE_TOKENS)
                or (
                    tok.isascii()
                    and ctok in _COLLAPSED_UNDO_SINGLE_TOKENS
                    and ctok not in english_exact_only
                )
            ):
                if prev_neg:
                    logger.info("FastPath gate_decision=gated_negated_imperative tok=%r", tok)
                    return FastPathMatch(
                        kind=FastPathIntentKind.NONE,
                        matched_entry=tok,
                        gate_decision="gated_negated_imperative",
                        normalized_transcript=cleaned,
                    )
                if _record_candidate(
                    FastPathIntentKind.UNDO,
                    tok,
                    "exact_indic_token_match",
                    is_edge,
                ):
                    break

            # Edit distance 1 ONLY for romanized Indic tokens (hi/te/ta) of length >= 5
            if tok.isascii() and len(ctok) >= 5:
                for canon in ROMANIZED_UNDO_CANONICAL:
                    if canon in english_exact_only:
                        continue
                    canon_col = _collapse_repeated_ascii_letters(canon)
                    if len(canon_col) >= 5 and _edit_distance_le_1(ctok, canon_col):
                        if prev_neg:
                            logger.info(
                                "FastPath gate_decision=gated_negated_imperative tok=%r", tok
                            )
                            return FastPathMatch(
                                kind=FastPathIntentKind.NONE,
                                matched_entry=canon,
                                gate_decision="gated_negated_imperative",
                                normalized_transcript=cleaned,
                            )
                        if _record_candidate(
                            FastPathIntentKind.UNDO,
                            canon,
                            "edit_distance_1_romanized_indic_len_ge_5",
                            is_edge,
                        ):
                            break
                if candidate_kind != FastPathIntentKind.NONE:
                    break

    # 7. Check single-token Stop matches
    if candidate_kind == FastPathIntentKind.NONE:
        for idx, (tok, ctok) in enumerate(zip(masked_tokens, masked_collapsed, strict=True)):
            prev_neg = idx > 0 and masked_tokens[idx - 1] in negation_particles
            is_edge = idx == 0 or idx == len(masked_tokens) - 1
            if tok in english_exact_only:
                if tok in STOP_SINGLE_TOKENS:
                    if prev_neg:
                        logger.info("FastPath gate_decision=gated_negated_imperative tok=%r", tok)
                        return FastPathMatch(
                            kind=FastPathIntentKind.NONE,
                            matched_entry=tok,
                            gate_decision="gated_negated_imperative",
                            normalized_transcript=cleaned,
                        )
                    if _record_candidate(
                        FastPathIntentKind.STOP,
                        tok,
                        "exact_english_token_match",
                        is_edge,
                    ):
                        break
                continue

            if (
                tok in STOP_SINGLE_TOKENS
                or (not tok.isascii() and ctok in _COLLAPSED_STOP_SINGLE_TOKENS)
                or (
                    tok.isascii()
                    and ctok in _COLLAPSED_STOP_SINGLE_TOKENS
                    and ctok not in english_exact_only
                )
            ):
                if prev_neg:
                    logger.info("FastPath gate_decision=gated_negated_imperative tok=%r", tok)
                    return FastPathMatch(
                        kind=FastPathIntentKind.NONE,
                        matched_entry=tok,
                        gate_decision="gated_negated_imperative",
                        normalized_transcript=cleaned,
                    )
                if _record_candidate(
                    FastPathIntentKind.STOP,
                    tok,
                    "exact_indic_token_match",
                    is_edge,
                ):
                    break

            # Edit distance 1 ONLY for romanized Indic tokens (hi/te/ta) of length >= 5
            if tok.isascii() and len(ctok) >= 5:
                for canon in ROMANIZED_STOP_CANONICAL:
                    if canon in english_exact_only:
                        continue
                    canon_col = _collapse_repeated_ascii_letters(canon)
                    if len(canon_col) >= 5 and _edit_distance_le_1(ctok, canon_col):
                        if prev_neg:
                            logger.info(
                                "FastPath gate_decision=gated_negated_imperative tok=%r", tok
                            )
                            return FastPathMatch(
                                kind=FastPathIntentKind.NONE,
                                matched_entry=canon,
                                gate_decision="gated_negated_imperative",
                                normalized_transcript=cleaned,
                            )
                        if _record_candidate(
                            FastPathIntentKind.STOP,
                            canon,
                            "edit_distance_1_romanized_indic_len_ge_5",
                            is_edge,
                        ):
                            break
                if candidate_kind != FastPathIntentKind.NONE:
                    break

    if candidate_kind == FastPathIntentKind.NONE and non_boundary_fallback is not None:
        _, fb_entry, fb_rule = non_boundary_fallback
        gate_msg = f"gated_max_tokens_{len(raw_tokens)}_gt_{max_tokens}_not_boundary"
        logger.info(
            "FastPath gate_decision=%s entry=%r rule=%s transcript=%r",
            gate_msg,
            fb_entry,
            fb_rule,
            cleaned,
        )
        return FastPathMatch(
            kind=FastPathIntentKind.NONE,
            matched_entry=fb_entry,
            matched_rule=fb_rule,
            gate_decision=gate_msg,
            normalized_transcript=cleaned,
        )

    if candidate_kind == FastPathIntentKind.NONE:
        logger.info("FastPath gate_decision=no_match transcript=%r", cleaned)
        return FastPathMatch(
            kind=FastPathIntentKind.NONE,
            gate_decision="no_match",
            normalized_transcript=cleaned,
        )

    # 9. State gate: act ONLY when state is ACTING, SPEAKING, or CORRECTING
    if current_state is not None:
        norm_state = current_state.strip().upper()
        if norm_state not in ("ACTING", "SPEAKING", "CORRECTING", "EXECUTING"):
            gate_msg = f"gated_state_{norm_state}"
            logger.info(
                "FastPath gate_decision=%s entry=%r rule=%s transcript=%r",
                gate_msg,
                candidate_entry,
                candidate_rule,
                cleaned,
            )
            return FastPathMatch(
                kind=FastPathIntentKind.NONE,
                matched_entry=candidate_entry,
                matched_rule=candidate_rule,
                gate_decision=gate_msg,
                normalized_transcript=cleaned,
            )
        allow_gate = f"allowed_state_{norm_state}"
    else:
        allow_gate = "allowed_unscoped"

    logger.info(
        "FastPath matched kind=%s entry=%r rule=%s gate_decision=%s transcript=%r",
        candidate_kind.value,
        candidate_entry,
        candidate_rule,
        allow_gate,
        cleaned,
    )
    return FastPathMatch(
        kind=candidate_kind,
        matched_entry=candidate_entry,
        matched_rule=candidate_rule,
        gate_decision=allow_gate,
        normalized_transcript=cleaned,
    )


# ---------------------------------------------------------------------------
# Transcript & Path Validation Helpers
# ---------------------------------------------------------------------------


def validate_transcript(
    transcript: str,
    max_chars: int = MAX_TRANSCRIPT_CHARS,
    defense_guard: Optional[DynamicDefenseGuard] = None,
) -> str:
    """Validate untrusted STT transcript before sending to the planner."""
    if not isinstance(transcript, str):
        raise IntentValidationError(
            code="TRANSCRIPT_INVALID_TYPE",
            message="Transcript must be a string.",
        )
    cleaned = transcript.strip()
    if not cleaned:
        raise IntentValidationError(
            code="TRANSCRIPT_EMPTY",
            message="Spoken transcript is empty; no command could be interpreted.",
            remediation="Speak a clear instruction and try again.",
        )
    if len(cleaned) > max_chars:
        raise IntentValidationError(
            code="TRANSCRIPT_TOO_LONG",
            message=f"Transcript length ({len(cleaned)} chars) exceeds limit ({max_chars}).",
        )
    if "\x00" in cleaned:
        raise IntentValidationError(
            code="TRANSCRIPT_NULL_BYTE",
            message="Null byte detected in transcript.",
        )

    guard = defense_guard or DynamicDefenseGuard()
    assessment = guard.evaluate_payload(cleaned)
    if assessment.is_threat_detected:
        cls_val = assessment.classification.value
        raise IntentValidationError(
            code="TRANSCRIPT_ADVERSARIAL_BLOCKED",
            message=f"Transcript blocked by adversarial shield ({cls_val}).",
        )
    return cleaned


def _validate_workspace_relative_path(
    raw_path: str,
    workspace_root: Path,
    must_not_exist: bool = False,
) -> Tuple[str, Path]:
    """Validate that a file path is a safe relative path confined inside workspace_root."""
    if not raw_path or not raw_path.strip():
        raise IntentValidationError(
            code="INTENT_EMPTY_PATH",
            message="File path argument cannot be empty.",
        )
    if "\x00" in raw_path:
        raise IntentValidationError(
            code="INTENT_NULL_BYTE",
            message="Null byte detected in file path.",
        )

    norm_str = raw_path.strip().replace("\\", "/")

    # Block drive letters, UNC paths, Alternate Data Streams, and absolute paths
    if ":" in norm_str or norm_str.startswith("/") or norm_str.startswith("\\"):
        raise IntentValidationError(
            code="INTENT_ABSOLUTE_OR_ADS_PATH_FORBIDDEN",
            message=f"Absolute paths and Alternate Data Streams are prohibited: '{raw_path}'.",
        )

    parts = [p for p in norm_str.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise IntentValidationError(
            code="INTENT_PATH_TRAVERSAL_BLOCKED",
            message=f"Path traversal token detected in '{raw_path}'.",
        )

    for part in parts:
        if part.lower() in FORBIDDEN_EXTERNAL_FOLDER_KEYWORDS:
            raise IntentValidationError(
                code="INTENT_EXTERNAL_DIRECTORY_UNSUPPORTED",
                message=(
                    f"Directory '{part}' is outside the authorized workspace scope "
                    f"and is not enabled in Milestone 3."
                ),
            )

    if not _SAFE_REL_FILENAME_PATTERN.match(norm_str):
        raise IntentValidationError(
            code="INTENT_UNSAFE_PATH_CHARS",
            message=f"Path '{raw_path}' contains unsupported characters.",
        )

    ws_resolved = workspace_root.resolve()
    canonical = (ws_resolved / norm_str).resolve()
    if canonical == ws_resolved or ws_resolved not in canonical.parents:
        raise IntentValidationError(
            code="INTENT_PATH_ESCAPES_WORKSPACE",
            message=f"Resolved path escapes workspace root '{ws_resolved}'.",
        )

    # Protect .winai internal directory
    winai_dir = (ws_resolved / ".winai").resolve()
    if canonical == winai_dir or winai_dir in canonical.parents:
        raise IntentValidationError(
            code="INTENT_PROTECTED_PATH",
            message="Access to internal '.winai' security directory is prohibited.",
        )

    if must_not_exist and canonical.exists():
        raise IntentValidationError(
            code="INTENT_FILE_OVERWRITE_PROHIBITED",
            message=(
                f"File '{norm_str}' already exists in workspace; overwriting existing "
                f"files is prohibited for create_workspace_file."
            ),
        )

    return norm_str, canonical


def validate_untrusted_intent(
    raw_output: Union[str, Dict[str, Any]],
    workspace_root: Path,
    require_approvals_for_writes_and_launches: bool = True,
) -> VoiceIntent:
    """Validate untrusted planner JSON/dict into a strict, host-authorized VoiceIntent."""
    if isinstance(raw_output, str):
        text = raw_output.strip()
        # Strip optional markdown code fences if an LLM wrapped its JSON output
        if text.startswith("```"):
            lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
        except Exception as e:
            raise IntentValidationError(
                code="INTENT_INVALID_JSON",
                message="Planner returned malformed JSON instead of a structured intent object.",
            ) from e
    elif isinstance(raw_output, dict):
        parsed = dict(raw_output)
    else:
        raise IntentValidationError(
            code="INTENT_INVALID_PAYLOAD_TYPE",
            message="Planner output must be a JSON string or dictionary.",
        )

    if not isinstance(parsed, dict):
        raise IntentValidationError(
            code="INTENT_NOT_OBJECT",
            message="Planner JSON output must be a top-level object.",
        )

    # Strip any model-supplied approval/risk override fields so they can never be trusted
    sanitized_dict = {
        k: v for k, v in parsed.items() if str(k).lower() not in UNTRUSTED_MODEL_AUTH_FIELDS
    }

    try:
        envelope = _UntrustedIntentEnvelope(**sanitized_dict)
    except ValidationError as e:
        raise IntentValidationError(
            code="INTENT_SCHEMA_VIOLATION",
            message=f"Intent envelope failed strict schema validation: {e}",
        ) from e

    # Verify action_type is in the 5-action allowlist
    try:
        action = AllowedVoiceAction(envelope.action_type.strip().lower())
    except ValueError as e:
        raise IntentValidationError(
            code="INTENT_UNSUPPORTED_ACTION",
            message=(
                f"Action '{envelope.action_type}' is not in the approved voice action "
                f"allowlist ({[a.value for a in AllowedVoiceAction]})."
            ),
        ) from e

    raw_args = envelope.arguments or {}
    for arg_k, arg_v in raw_args.items():
        if isinstance(arg_v, str) and "\x00" in arg_v:
            raise IntentValidationError(
                code="INTENT_NULL_BYTE",
                message=f"Null byte detected in argument '{arg_k}'.",
            )

    intent_id = (envelope.intent_id or "").strip() or f"vint_{secrets.token_hex(6)}"
    engine_tool = ACTION_TO_ENGINE_TOOL[action]

    # Validate per-action arguments and compute trusted host-side risk & confirmation flags
    if action == AllowedVoiceAction.SYSTEM_INFO:
        try:
            validated_args = SystemInfoArgs(**raw_args).model_dump()
        except ValidationError as e:
            raise IntentValidationError("INTENT_INVALID_ARGS", str(e)) from e
        target_res = "system"
        risk = RiskTier.LOW
        req_conf = False
        default_summary = "Inspect Windows system hardware and memory overview."

    elif action == AllowedVoiceAction.LIST_APPROVED_APPS:
        try:
            validated_args = ListApprovedAppsArgs(**raw_args).model_dump()
        except ValidationError as e:
            raise IntentValidationError("INTENT_INVALID_ARGS", str(e)) from e
        target_res = "approved_apps"
        risk = RiskTier.LOW
        req_conf = False
        default_summary = "List explicitly whitelisted Windows applications."

    elif action == AllowedVoiceAction.LAUNCH_APP:
        try:
            app_args = LaunchAppArgs(**raw_args)
        except ValidationError as e:
            raise IntentValidationError("INTENT_INVALID_ARGS", str(e)) from e

        norm_app_id = app_args.app_id.strip().lower()
        if norm_app_id == "calculator":
            norm_app_id = "calc"
        if not _APP_ID_PATTERN.match(norm_app_id):
            raise IntentValidationError(
                code="INTENT_INVALID_APP_ID",
                message=f"Invalid application identifier '{app_args.app_id}'.",
            )
        validated_args = {"app_id": norm_app_id}
        target_res = norm_app_id
        risk = RiskTier.LOW
        req_conf = bool(require_approvals_for_writes_and_launches)
        default_summary = f"Launch whitelisted application '{norm_app_id}'."

    elif action == AllowedVoiceAction.READ_WORKSPACE_FILE:
        try:
            read_args = ReadWorkspaceFileArgs(**raw_args)
        except ValidationError as e:
            raise IntentValidationError("INTENT_INVALID_ARGS", str(e)) from e

        rel_path, canonical_path = _validate_workspace_relative_path(
            read_args.path, workspace_root=workspace_root, must_not_exist=False
        )
        validated_args = {"path": rel_path, "max_bytes": read_args.max_bytes}
        target_res = str(canonical_path)
        risk = RiskTier.LOW
        req_conf = False
        default_summary = f"Read workspace file '{rel_path}'."

    elif action == AllowedVoiceAction.CREATE_WORKSPACE_FILE:
        try:
            create_args = CreateWorkspaceFileArgs(**raw_args)
        except ValidationError as e:
            raise IntentValidationError("INTENT_INVALID_ARGS", str(e)) from e

        rel_path, canonical_path = _validate_workspace_relative_path(
            create_args.path, workspace_root=workspace_root, must_not_exist=True
        )
        validated_args = {"path": rel_path, "content": create_args.content}
        target_res = str(canonical_path)
        risk = RiskTier.HIGH
        req_conf = True
        default_summary = (
            f"Create new workspace file '{rel_path}' ({len(create_args.content)} chars)."
        )

    elif action == AllowedVoiceAction.TYPE_IN_NOTEPAD:
        try:
            type_args = TypeInNotepadArgs(**raw_args)
        except ValidationError as e:
            raise IntentValidationError("INTENT_INVALID_ARGS", str(e)) from e

        validated_args = {
            "window_title": "Notepad",
            "text": type_args.text,
            "replace_last_span": bool(type_args.replace_last_span),
        }
        target_res = "Notepad"
        risk = RiskTier.LOW
        req_conf = False
        default_summary = f"Write text into Notepad ({len(type_args.text)} chars)."

    else:
        raise IntentValidationError("INTENT_UNSUPPORTED_ACTION", f"Unsupported action: {action}")

    summary = sanitize_error_message(envelope.user_facing_summary.strip() or default_summary)

    return VoiceIntent(
        intent_id=intent_id,
        action_type=action,
        engine_tool_name=engine_tool,
        arguments=validated_args,
        target_resource=target_res,
        user_facing_summary=summary,
        risk_category=risk,
        requires_confirmation=req_conf,
        reply_language=envelope.reply_language or "en-IN",
    )


# ---------------------------------------------------------------------------
# VoiceIntentPlanner (Untrusted Interpreter with Timeout & Deterministic Mode)
# ---------------------------------------------------------------------------

PlannerCallable = Callable[[str], Coroutine[Any, Any, Union[str, Dict[str, Any]]]]


class VoiceIntentPlanner:
    """Converts a validated transcript into a structured VoiceIntent candidate.

    Supports:
    - Custom async `planner_fn` (for deterministic tests or custom adapters).
    - `BaseModelProvider` (e.g., `GeminiAdapter`) with bounded timeout.
    - Built-in deterministic pattern interpreter for the 5 allowlisted actions when
      no external LLM provider is supplied (never guesses destructive actions).
    """

    def __init__(
        self,
        workspace_root: Path,
        llm_provider: Optional[BaseModelProvider] = None,
        planner_fn: Optional[PlannerCallable] = None,
        timeout_seconds: float = 10.0,
        require_approvals: bool = True,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.llm_provider = llm_provider
        self.planner_fn = planner_fn
        self.timeout_seconds = timeout_seconds
        self.require_approvals = require_approvals
        self._defense = DynamicDefenseGuard()

    async def plan_transcript(self, transcript: str) -> VoiceIntent:
        """Validate transcript, invoke planner within timeout, and return validated VoiceIntent."""
        clean_transcript = validate_transcript(transcript, defense_guard=self._defense)

        try:
            if self.planner_fn is not None:
                raw_candidate = await asyncio.wait_for(
                    self.planner_fn(clean_transcript),
                    timeout=self.timeout_seconds,
                )
            elif self.llm_provider is not None:
                raw_candidate = await asyncio.wait_for(
                    self._call_llm_planner(clean_transcript),
                    timeout=self.timeout_seconds,
                )
            else:
                raw_candidate = self._deterministic_interpret(clean_transcript)
        except asyncio.TimeoutError as e:
            raise IntentValidationError(
                code="PLANNER_TIMEOUT",
                message=f"Intent planner timed out after {self.timeout_seconds}s.",
            ) from e

        return validate_untrusted_intent(
            raw_output=raw_candidate,
            workspace_root=self.workspace_root,
            require_approvals_for_writes_and_launches=self.require_approvals,
        )

    async def plan_correction(
        self,
        correction_transcript: str,
        current_task_state: Dict[str, Any],
    ) -> VoiceIntent:
        """Modify the current plan/editor content in-place without restarting from scratch.

        Never returns placeholder text; raises IntentValidationError so the caller speaks a
        clarification if no active text exists or if the planner fails/returns empty.
        """
        clean_corr = validate_transcript(correction_transcript, defense_guard=self._defense)
        previous_text = str(current_task_state.get("current_text") or "").strip()

        if not previous_text:
            raise IntentValidationError(
                code="CORRECTION_NO_ACTIVE_TEXT",
                message=(
                    "No text has been dictated into Notepad yet. "
                    "Please dictate text first before asking for a correction."
                ),
            )

        if self.llm_provider is not None:
            try:
                sys_prompt = (
                    "You are modifying an in-progress Windows Notepad document based on a "
                    "user's spoken correction (e.g. 'make it clearer', 'thoda saaf karo', "
                    "'inka clear ga cheyyi'). Do NOT restart from scratch unless requested. "
                    "Return ONLY the revised plain-text content."
                )
                user_msg = (
                    f"Current text:\n{previous_text}\n\nUser correction instruction:\n{clean_corr}"
                )
                resp = await asyncio.wait_for(
                    self.llm_provider.complete(
                        messages=[
                            ChatMessage(role="system", content=sys_prompt),
                            ChatMessage(role="user", content=user_msg),
                        ],
                        temperature=0.2,
                        max_tokens=300,
                    ),
                    timeout=self.timeout_seconds,
                )
            except Exception as e:
                raise IntentValidationError(
                    code="PLANNER_CLARIFICATION_REQUIRED",
                    message=(
                        "Could not generate a revised draft. Please repeat or clarify your edit."
                    ),
                ) from e

            revised = (resp.content or "").strip()
            if not revised:
                raise IntentValidationError(
                    code="PLANNER_EMPTY_DRAFT",
                    message="Planner returned an empty draft. Please clarify what to change.",
                )
            return validate_untrusted_intent(
                raw_output={
                    "action_type": AllowedVoiceAction.TYPE_IN_NOTEPAD.value,
                    "arguments": {"text": revised, "replace_last_span": True},
                    "user_facing_summary": f"Updated text in-place: {revised[:60]}",
                },
                workspace_root=self.workspace_root,
                require_approvals_for_writes_and_launches=self.require_approvals,
            )

        # Deterministic in-place formatting of existing agent-typed text (no placeholder strings)
        sentences = [s.strip() for s in re.split(r"[.\n]+", previous_text) if s.strip()]
        if not sentences:
            raise IntentValidationError(
                code="CORRECTION_NO_ACTIVE_TEXT",
                message="No active text to clarify. Please dictate a sentence first.",
            )
        formatted_lines = [
            (s[0].upper() + s[1:] if len(s) > 1 else s.upper()) + "." for s in sentences
        ]
        revised_text = (
            "\n".join(f"- {ln}" for ln in formatted_lines)
            if len(formatted_lines) > 1
            else formatted_lines[0]
        )

        return validate_untrusted_intent(
            raw_output={
                "action_type": AllowedVoiceAction.TYPE_IN_NOTEPAD.value,
                "arguments": {"text": revised_text, "replace_last_span": True},
                "user_facing_summary": f"Modified active document ({len(revised_text)} chars).",
            },
            workspace_root=self.workspace_root,
            require_approvals_for_writes_and_launches=self.require_approvals,
        )

    async def _call_llm_planner(self, transcript: str) -> str:
        assert self.llm_provider is not None
        sys_prompt = (
            "You are a strict JSON intent parser for WinAI-OE. "
            "Respond ONLY with a single JSON object containing keys: "
            "action_type (one of: system_info, list_approved_apps, launch_app, "
            "read_workspace_file, create_workspace_file), arguments (object), "
            "and user_facing_summary (string). "
            "If the request asks for any other action, set action_type to 'unsupported'."
        )
        resp = await self.llm_provider.complete(
            messages=[
                ChatMessage(role="system", content=sys_prompt),
                ChatMessage(role="user", content=transcript),
            ],
            temperature=0.0,
            max_tokens=256,
        )
        return resp.content

    @staticmethod
    def _deterministic_interpret(transcript: str) -> Dict[str, Any]:
        """Deterministic interpreter for the 5 allowlisted commands (no unsafe fallbacks)."""
        lower = transcript.lower().strip()

        # 1. Explicit rejection of excluded / destructive intents (never guess a safe fallback)
        forbidden_keywords = (
            "delete",
            "remove",
            "overwrite",
            "powershell",
            "cmd",
            "terminal",
            "kill",
            "terminate",
            "close",
            "deploy",
            "email",
            "send resume",
        )
        if any(re.search(rf"\b{re.escape(kw)}\b", lower) for kw in forbidden_keywords):
            return {
                "action_type": "unsupported_destructive_request",
                "arguments": {},
                "user_facing_summary": "Unsupported or restricted action requested.",
            }

        for ext_dir in ("desktop", "documents", "downloads"):
            if re.search(rf"\b{ext_dir}\b", lower):
                return {
                    "action_type": "create_workspace_file",
                    "arguments": {"path": f"{ext_dir}/blocked.txt", "content": ""},
                }

        # 2. System overview / RAM / hardware status
        if any(
            phrase in lower
            for phrase in (
                "system info",
                "system overview",
                "ram status",
                "memory status",
                "hardware specs",
            )
        ):
            return {
                "action_type": AllowedVoiceAction.SYSTEM_INFO.value,
                "arguments": {"detailed": False},
                "user_facing_summary": "Report Windows system overview.",
            }

        # 3. List approved applications
        if any(
            phrase in lower
            for phrase in (
                "list approved apps",
                "approved applications",
                "permitted apps",
                "what apps can you open",
            )
        ):
            return {
                "action_type": AllowedVoiceAction.LIST_APPROVED_APPS.value,
                "arguments": {},
                "user_facing_summary": "List approved Windows applications.",
            }

        # 4. Launch application (English / Tamil / Telugu / Hindi / Kannada code-mixed)
        has_open_verb = any(
            v in lower
            for v in (
                "open",
                "launch",
                "start",
                "pannu",
                "thira",
                "ఓపెన్",
                "ओपन",
                "ಓಪನ್",
                "திற",
                "ஓபன்",
                "cheyyi",
                "chesi",
                "maadi",
                "karo",
            )
        )
        if has_open_verb:
            for app_token, canonical_id in (
                ("notepad", "notepad"),
                ("note pad", "notepad"),
                ("note paid", "notepad"),
                ("నోట్ ప్యాడ్", "notepad"),
                ("నోట్‌ప్యాడ్", "notepad"),
                ("नोटपैड", "notepad"),
                ("ನೋಟ್ ಪ್ಯಾಡ್", "notepad"),
                ("நோட்பேட்", "notepad"),
                ("நோட் பேட்", "notepad"),
                ("calculator", "calc"),
                ("calc", "calc"),
                ("கால்குலேட்டர்", "calc"),
                ("chrome", "chrome"),
                ("க்ரோம்", "chrome"),
                ("vscode", "vscode"),
                ("vs code", "vscode"),
                ("edge", "edge"),
            ):
                if app_token in lower:
                    return {
                        "action_type": AllowedVoiceAction.LAUNCH_APP.value,
                        "arguments": {"app_id": canonical_id},
                        "user_facing_summary": f"Launch {canonical_id}.",
                    }
            # Extract unknown app name so policy/whitelist rejects it explicitly
            m_app = re.search(r"\b(?:open|launch|start)\s+([a-z0-9_.-]+)", lower)
            if m_app:
                return {
                    "action_type": AllowedVoiceAction.LAUNCH_APP.value,
                    "arguments": {"app_id": m_app.group(1)},
                    "user_facing_summary": f"Launch {m_app.group(1)}.",
                }

        # 5. Read workspace file
        m_read = re.search(
            r"\b(?:read|show|inspect)\s+(?:file\s+)?([A-Za-z0-9_./-]+)", transcript, re.IGNORECASE
        )
        if m_read and "." in m_read.group(1):
            return {
                "action_type": AllowedVoiceAction.READ_WORKSPACE_FILE.value,
                "arguments": {"path": m_read.group(1).rstrip(".,")},
                "user_facing_summary": f"Read workspace file {m_read.group(1).rstrip('.,')}.",
            }

        # 6. Create workspace file
        m_create = re.search(
            r"\b(?:create|write)\s+(?:file\s+)?([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)(?:\s+with\s+content\s+(.+))?$",
            transcript.strip(),
            re.IGNORECASE,
        )
        if m_create:
            fname = m_create.group(1).strip()
            body = (m_create.group(2) or "").strip().strip("'\"")
            return {
                "action_type": AllowedVoiceAction.CREATE_WORKSPACE_FILE.value,
                "arguments": {"path": fname, "content": body},
                "user_facing_summary": f"Create workspace file {fname}.",
            }

        # 7. Type / dictate into Notepad (English, Tamil, Telugu, Malayalam, Hindi, Kannada)
        m_type = re.search(
            r"^(?:please\s+)?(?:write|type|note\s+down|dictate|ezhudhu|rayi|rayandi|ezhuthu|likho|bareyiri)\s+(?:in\s+notepad\s+)?(?:that\s+)?(.+)$",
            transcript.strip(),
            re.IGNORECASE | re.DOTALL,
        )
        if m_type:
            body_text = m_type.group(1).strip()
            return {
                "action_type": AllowedVoiceAction.TYPE_IN_NOTEPAD.value,
                "arguments": {"text": body_text},
                "user_facing_summary": f"Write in Notepad: {body_text[:60]}",
            }

        return {
            "action_type": "unrecognized_intent",
            "arguments": {},
            "user_facing_summary": "Could not safely determine a supported action.",
        }
