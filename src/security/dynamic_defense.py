"""Dynamic defense and active adversarial shielding subsystem.

Provides:
1. Real-time prompt injection & jailbreak detection.
2. In-flight input/output sanitization.
3. Sub-agent permission boundary confinement.
4. Fail-closed threat neutralization.
"""

from enum import Enum
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class ThreatClassification(str, Enum):
    CLEAN = "CLEAN"
    DIRECT_JAILBREAK = "DIRECT_JAILBREAK"
    INDIRECT_PROMPT_INJECTION = "INDIRECT_PROMPT_INJECTION"
    COMMAND_ESCAPE_ATTEMPT = "COMMAND_ESCAPE_ATTEMPT"
    PRIVILEGE_ESCALATION_ATTEMPT = "PRIVILEGE_ESCALATION_ATTEMPT"


class ThreatAssessment(BaseModel):
    is_threat_detected: bool
    classification: ThreatClassification
    confidence: float
    detected_patterns: List[str]
    mitigation_applied: str


class DynamicDefenseGuard:
    """Active heuristic and signature-based threat interceptor."""

    # Heuristic signature patterns for prompt injection and jailbreaks
    INJECTION_PATTERNS = [
        (re.compile(r"ignore\s+(all\s+)?(prior|previous)\s+instructions", re.IGNORECASE), ThreatClassification.DIRECT_JAILBREAK),
        (re.compile(r"you\s+are\s+now\s+(in\s+)?(unrestricted|dan|developer)\s+mode", re.IGNORECASE), ThreatClassification.DIRECT_JAILBREAK),
        (re.compile(r"\[system\s*:\s*[^\]]+\]", re.IGNORECASE), ThreatClassification.INDIRECT_PROMPT_INJECTION),
        (re.compile(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL), ThreatClassification.INDIRECT_PROMPT_INJECTION),
        (re.compile(r";\s*(rm\s+-rf|format|del\s+/f|powershell\s+-enc)", re.IGNORECASE), ThreatClassification.COMMAND_ESCAPE_ATTEMPT),
        (re.compile(r"(sudo|runas\s+/user:administrator|elevate|takeown)", re.IGNORECASE), ThreatClassification.PRIVILEGE_ESCALATION_ATTEMPT),
        (re.compile(r"(\.\./){3,}|(\\.\.\\){3,}", re.IGNORECASE), ThreatClassification.COMMAND_ESCAPE_ATTEMPT),
    ]

    def evaluate_payload(self, text: str) -> ThreatAssessment:
        """Scan raw input or tool output for adversarial injection patterns."""
        if not text:
            return ThreatAssessment(
                is_threat_detected=False,
                classification=ThreatClassification.CLEAN,
                confidence=0.0,
                detected_patterns=[],
                mitigation_applied="None required",
            )

        detected = []
        highest_threat = ThreatClassification.CLEAN

        for pattern, threat_type in self.INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                detected.append(match.group(0))
                highest_threat = threat_type

        if detected:
            return ThreatAssessment(
                is_threat_detected=True,
                classification=highest_threat,
                confidence=0.95,
                detected_patterns=detected,
                mitigation_applied="Payload blocked and session flagged for containment.",
            )

        return ThreatAssessment(
            is_threat_detected=False,
            classification=ThreatClassification.CLEAN,
            confidence=0.0,
            detected_patterns=[],
            mitigation_applied="None required",
        )

    def sanitize_untrusted_content(self, raw_content: str) -> str:
        """Neutralize known prompt injection markers in retrieved external content."""
        sanitized = raw_content
        for pattern, _ in self.INJECTION_PATTERNS:
            sanitized = pattern.sub("[BLOCKED_INJECTION_MARKER]", sanitized)
        return sanitized
