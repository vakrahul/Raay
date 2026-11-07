"""Truthful Voice Capability Registry for WinAI-OE (Milestone 1).

Tracks Speech-to-Text (STT) and Text-to-Speech (TTS) independently with five
strictly distinguished states:
- UNAVAILABLE: provider is absent, disabled, or failed connectivity verification.
- UNCONFIGURED: provider instance exists, but required credentials/configuration are missing.
- CONFIGURED: credentials/configuration exist locally, but no connectivity check has run.
- REACHABLE: a non-destructive connectivity/authentication check succeeded.
- OPERATIONALLY_TESTED: a real STT or TTS operation completed and was verified.

Guarantees:
- Zero network I/O during initialization or status inspection (`get_report()`).
- Zero secret leakage in serialized reports (`to_dict()` passes through `redact_secrets()`).
- Full dependency injection for offline deterministic testing.
"""

import time
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.security.audit_logger import redact_secrets
from src.voice.base import (
    SUPPORTED_STT_LANGUAGES,
    SUPPORTED_STT_SAMPLE_RATES,
    SUPPORTED_TTS_LANGUAGES,
    SUPPORTED_TTS_SAMPLE_RATES,
    BaseSTTProvider,
    BaseTTSProvider,
    sanitize_error_message,
)


class CapabilityStatus(str, Enum):
    UNAVAILABLE = "unavailable"
    UNCONFIGURED = "unconfigured"
    CONFIGURED = "configured"
    REACHABLE = "reachable"
    OPERATIONALLY_TESTED = "operationally_tested"


class ServiceCapabilityReport(BaseModel):
    """Independent capability report for either STT or TTS."""

    model_config = ConfigDict(extra="forbid")

    capability: str
    provider_name: str
    models: List[str] = Field(default_factory=list)
    status: CapabilityStatus
    reason: str
    supported_languages: List[str] = Field(default_factory=list)
    supported_sample_rates: List[int] = Field(default_factory=list)
    transports: List[str] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)
    last_checked_at: Optional[float] = None


class CapabilityRegistry:
    """Truthful capability registry for STT and TTS providers."""

    def __init__(
        self,
        stt_provider: Optional[BaseSTTProvider] = None,
        tts_provider: Optional[BaseTTSProvider] = None,
    ):
        self._stt_provider = stt_provider
        self._tts_provider = tts_provider

        self._stt_reachable: Optional[bool] = None
        self._stt_reason: Optional[str] = None
        self._stt_checked_at: Optional[float] = None
        self._stt_operationally_tested: bool = False

        self._tts_reachable: Optional[bool] = None
        self._tts_reason: Optional[str] = None
        self._tts_checked_at: Optional[float] = None
        self._tts_operationally_tested: bool = False

    def set_stt_provider(self, provider: Optional[BaseSTTProvider]) -> None:
        self._stt_provider = provider
        self._stt_reachable = None
        self._stt_reason = None
        self._stt_checked_at = None
        self._stt_operationally_tested = False

    def set_tts_provider(self, provider: Optional[BaseTTSProvider]) -> None:
        self._tts_provider = provider
        self._tts_reachable = None
        self._tts_reason = None
        self._tts_checked_at = None
        self._tts_operationally_tested = False

    def mark_stt_operationally_tested(
        self, reason: str = "Verified STT operation completed."
    ) -> None:
        self._stt_operationally_tested = True
        self._stt_reachable = True
        self._stt_reason = sanitize_error_message(reason)
        self._stt_checked_at = time.time()

    def mark_tts_operationally_tested(
        self, reason: str = "Verified TTS synthesis completed."
    ) -> None:
        self._tts_operationally_tested = True
        self._tts_reachable = True
        self._tts_reason = sanitize_error_message(reason)
        self._tts_checked_at = time.time()

    @staticmethod
    def _is_provider_configured(provider: Any) -> bool:
        if provider is None:
            return False
        if hasattr(provider, "is_configured"):
            return bool(provider.is_configured)
        if hasattr(provider, "config") and hasattr(provider.config, "is_configured"):
            return bool(provider.config.is_configured)
        return True

    @staticmethod
    def _is_provider_operationally_tested(provider: Any, registry_flag: bool) -> bool:
        if registry_flag:
            return True
        if provider is not None and hasattr(provider, "is_operationally_tested"):
            return bool(provider.is_operationally_tested)
        return False

    def _resolve_stt_status(self) -> ServiceCapabilityReport:
        if self._stt_provider is None:
            return ServiceCapabilityReport(
                capability="stt",
                provider_name="none",
                models=[],
                status=CapabilityStatus.UNAVAILABLE,
                reason="No STT provider is registered.",
            )

        if not self._is_provider_configured(self._stt_provider):
            return ServiceCapabilityReport(
                capability="stt",
                provider_name="gnani-prisma",
                models=["gnani-prisma-v2.5", "vachana-audio-intelligence-v2"],
                status=CapabilityStatus.UNCONFIGURED,
                reason="Gnani API key is not configured.",
                supported_languages=sorted(SUPPORTED_STT_LANGUAGES),
                supported_sample_rates=sorted(SUPPORTED_STT_SAMPLE_RATES),
                transports=["rest", "websocket"],
                features=[
                    "itn",
                    "word_boosting",
                    "substitution",
                    "vad",
                ],
                last_checked_at=self._stt_checked_at,
            )

        if self._is_provider_operationally_tested(
            self._stt_provider, self._stt_operationally_tested
        ):
            return ServiceCapabilityReport(
                capability="stt",
                provider_name="gnani-prisma",
                models=["gnani-prisma-v2.5", "vachana-audio-intelligence-v2"],
                status=CapabilityStatus.OPERATIONALLY_TESTED,
                reason=self._stt_reason
                or "STT provider completed a verified transcription operation.",
                supported_languages=sorted(SUPPORTED_STT_LANGUAGES),
                supported_sample_rates=sorted(SUPPORTED_STT_SAMPLE_RATES),
                transports=["rest", "websocket"],
                features=[
                    "itn",
                    "word_boosting",
                    "substitution",
                    "vad",
                ],
                last_checked_at=self._stt_checked_at,
            )

        if self._stt_reachable is True:
            return ServiceCapabilityReport(
                capability="stt",
                provider_name="gnani-prisma",
                models=["gnani-prisma-v2.5", "vachana-audio-intelligence-v2"],
                status=CapabilityStatus.REACHABLE,
                reason=self._stt_reason or "Non-destructive connectivity check succeeded.",
                supported_languages=sorted(SUPPORTED_STT_LANGUAGES),
                supported_sample_rates=sorted(SUPPORTED_STT_SAMPLE_RATES),
                transports=["rest", "websocket"],
                features=[
                    "itn",
                    "word_boosting",
                    "substitution",
                    "vad",
                ],
                last_checked_at=self._stt_checked_at,
            )

        if self._stt_reachable is False:
            return ServiceCapabilityReport(
                capability="stt",
                provider_name="gnani-prisma",
                models=["gnani-prisma-v2.5", "vachana-audio-intelligence-v2"],
                status=CapabilityStatus.UNAVAILABLE,
                reason=self._stt_reason or "Connectivity check failed.",
                supported_languages=sorted(SUPPORTED_STT_LANGUAGES),
                supported_sample_rates=sorted(SUPPORTED_STT_SAMPLE_RATES),
                transports=["rest", "websocket"],
                features=[
                    "itn",
                    "word_boosting",
                    "substitution",
                    "vad",
                ],
                last_checked_at=self._stt_checked_at,
            )

        return ServiceCapabilityReport(
            capability="stt",
            provider_name="gnani-prisma",
            models=["gnani-prisma-v2.5", "vachana-audio-intelligence-v2"],
            status=CapabilityStatus.CONFIGURED,
            reason="Credentials configured locally; connectivity has not been verified yet.",
            supported_languages=sorted(SUPPORTED_STT_LANGUAGES),
            supported_sample_rates=sorted(SUPPORTED_STT_SAMPLE_RATES),
            transports=["rest", "websocket"],
            features=[
                "itn",
                "word_boosting",
                "substitution",
                "vad",
            ],
            last_checked_at=self._stt_checked_at,
        )

    def _resolve_tts_status(self) -> ServiceCapabilityReport:
        if self._tts_provider is None:
            return ServiceCapabilityReport(
                capability="tts",
                provider_name="none",
                models=[],
                status=CapabilityStatus.UNAVAILABLE,
                reason="No TTS provider is registered.",
            )

        if not self._is_provider_configured(self._tts_provider):
            return ServiceCapabilityReport(
                capability="tts",
                provider_name="gnani-timbre",
                models=["timbre-v2.5", "timbre-v3.0"],
                status=CapabilityStatus.UNCONFIGURED,
                reason="Gnani API key is not configured.",
                supported_languages=sorted(SUPPORTED_TTS_LANGUAGES),
                supported_sample_rates=sorted(SUPPORTED_TTS_SAMPLE_RATES),
                transports=["rest", "sse"],
                features=[
                    "audio_tags",
                    "text_normalization",
                    "wav_header_normalization",
                    "raw_pcm",
                ],
                last_checked_at=self._tts_checked_at,
            )

        if self._is_provider_operationally_tested(
            self._tts_provider, self._tts_operationally_tested
        ):
            return ServiceCapabilityReport(
                capability="tts",
                provider_name="gnani-timbre",
                models=["timbre-v2.5", "timbre-v3.0"],
                status=CapabilityStatus.OPERATIONALLY_TESTED,
                reason=self._tts_reason or "TTS provider completed a verified synthesis operation.",
                supported_languages=sorted(SUPPORTED_TTS_LANGUAGES),
                supported_sample_rates=sorted(SUPPORTED_TTS_SAMPLE_RATES),
                transports=["rest", "sse"],
                features=[
                    "audio_tags",
                    "text_normalization",
                    "wav_header_normalization",
                    "raw_pcm",
                ],
                last_checked_at=self._tts_checked_at,
            )

        if self._tts_reachable is True:
            return ServiceCapabilityReport(
                capability="tts",
                provider_name="gnani-timbre",
                models=["timbre-v2.5", "timbre-v3.0"],
                status=CapabilityStatus.REACHABLE,
                reason=self._tts_reason or "Non-destructive connectivity check succeeded.",
                supported_languages=sorted(SUPPORTED_TTS_LANGUAGES),
                supported_sample_rates=sorted(SUPPORTED_TTS_SAMPLE_RATES),
                transports=["rest", "sse"],
                features=[
                    "audio_tags",
                    "text_normalization",
                    "wav_header_normalization",
                    "raw_pcm",
                ],
                last_checked_at=self._tts_checked_at,
            )

        if self._tts_reachable is False:
            return ServiceCapabilityReport(
                capability="tts",
                provider_name="gnani-timbre",
                models=["timbre-v2.5", "timbre-v3.0"],
                status=CapabilityStatus.UNAVAILABLE,
                reason=self._tts_reason or "Connectivity check failed.",
                supported_languages=sorted(SUPPORTED_TTS_LANGUAGES),
                supported_sample_rates=sorted(SUPPORTED_TTS_SAMPLE_RATES),
                transports=["rest", "sse"],
                features=[
                    "audio_tags",
                    "text_normalization",
                    "wav_header_normalization",
                    "raw_pcm",
                ],
                last_checked_at=self._tts_checked_at,
            )

        return ServiceCapabilityReport(
            capability="tts",
            provider_name="gnani-timbre",
            models=["timbre-v2.5", "timbre-v3.0"],
            status=CapabilityStatus.CONFIGURED,
            reason="Credentials configured locally; connectivity has not been verified yet.",
            supported_languages=sorted(SUPPORTED_TTS_LANGUAGES),
            supported_sample_rates=sorted(SUPPORTED_TTS_SAMPLE_RATES),
            transports=["rest", "sse"],
            features=["audio_tags", "text_normalization", "wav_header_normalization", "raw_pcm"],
            last_checked_at=self._tts_checked_at,
        )

    async def verify_stt_connectivity(self) -> ServiceCapabilityReport:
        """Run a non-destructive connectivity check for STT only."""
        if self._stt_provider is None or not self._is_provider_configured(self._stt_provider):
            return self._resolve_stt_status()

        self._stt_checked_at = time.time()
        try:
            ok, reason = await self._stt_provider.health_check()
            self._stt_reachable = bool(ok)
            self._stt_reason = sanitize_error_message(reason)
        except Exception as e:
            self._stt_reachable = False
            self._stt_reason = f"Health check failed: {sanitize_error_message(str(e))}"

        return self._resolve_stt_status()

    async def verify_tts_connectivity(self) -> ServiceCapabilityReport:
        """Run a non-destructive connectivity check for TTS only."""
        if self._tts_provider is None or not self._is_provider_configured(self._tts_provider):
            return self._resolve_tts_status()

        self._tts_checked_at = time.time()
        try:
            ok, reason = await self._tts_provider.health_check()
            self._tts_reachable = bool(ok)
            self._tts_reason = sanitize_error_message(reason)
        except Exception as e:
            self._tts_reachable = False
            self._tts_reason = f"Health check failed: {sanitize_error_message(str(e))}"

        return self._resolve_tts_status()

    async def verify_connectivity(self) -> Dict[str, Any]:
        """Run non-destructive connectivity probes for both STT and TTS independently."""
        await self.verify_stt_connectivity()
        await self.verify_tts_connectivity()
        return self.to_dict()

    def get_stt_report(self) -> ServiceCapabilityReport:
        """Return current STT capability status without making network calls."""
        return self._resolve_stt_status()

    def get_tts_report(self) -> ServiceCapabilityReport:
        """Return current TTS capability status without making network calls."""
        return self._resolve_tts_status()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize independent STT and TTS capability reports with guaranteed secret redaction."""
        stt_report = self._resolve_stt_status().model_dump()
        tts_report = self._resolve_tts_status().model_dump()
        raw = {
            "stt": stt_report,
            "tts": tts_report,
        }
        return redact_secrets(raw)
