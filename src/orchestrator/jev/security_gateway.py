"""JEV security gateway (Stage 6).

CRITICAL: JEV is a decision component, NOT a security authority. Every
JEV-derived tool call, agent pick, or model pick passes through the same
host-side authorization pipeline as any other untrusted input:

  1. Adversarial screen (DynamicDefenseGuard) — injections fail closed.
  2. Schema validation (ActionValidator) — malformed calls fail closed.
  3. Policy evaluation (SecurityPolicyEngine) — ALLOW / DENY /
     REQUIRE_APPROVAL, with single-use approval nonces.

A JEV recommendation can never grant permissions, override approvals,
disable sandboxing, or authorize sensitive actions by itself.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from src.security.action_validator import ActionValidator
from src.security.dynamic_defense import DynamicDefenseGuard
from src.security.policy_engine import (
    ActionEvaluationResult,
    PolicyDecision,
    SecurityPolicyEngine,
)


class GatewayVerdict(BaseModel):
    """Final authorization outcome for one JEV-derived tool call."""

    model_config = ConfigDict(extra="forbid")

    decision: PolicyDecision
    reason: str
    risk_tier: str
    screened_threat: Optional[str] = None
    approval_consumed: bool = False


class JevSecurityGateway:
    """Enforces host-side authorization over all JEV-derived actions."""

    def __init__(
        self,
        policy_engine: SecurityPolicyEngine,
        defense_guard: Optional[DynamicDefenseGuard] = None,
    ):
        self.policy_engine = policy_engine
        self.defense = defense_guard or DynamicDefenseGuard()

    def screen_text(self, text: str) -> Optional[str]:
        """Return the threat classification string, or None when clean."""
        assessment = self.defense.evaluate_payload(text)
        if assessment.is_threat_detected:
            return assessment.classification.value
        return None

    @staticmethod
    def _has_shell_escape(value: Any) -> bool:
        """Detect shell metacharacters smuggled inside argument arrays.

        In shell=False execution a standalone `;`, `|`, `&&`, or command
        substitution has no legitimate purpose and signals injection.
        """
        if isinstance(value, str):
            stripped = value.strip()
            if stripped in (";", "|", "&&", "||"):
                return True
            return "$(" in value or "`" in value
        if isinstance(value, (list, tuple)):
            return any(JevSecurityGateway._has_shell_escape(v) for v in value)
        if isinstance(value, dict):
            return any(JevSecurityGateway._has_shell_escape(v) for v in value.values())
        return False

    def authorize_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        session_id: str,
        agent_id: str,
        approval_nonce: Optional[str] = None,
        jev_source: str = "jev",
    ) -> GatewayVerdict:
        """Authorize one JEV-recommended tool invocation.

        `jev_source` is provenance metadata only — it confers zero trust.
        """
        _ = jev_source  # explicit: provenance never influences the decision

        # Gate 1: adversarial screen over the serialized proposal.
        threat = self.screen_text(f"{tool_name} {arguments}")
        if threat is not None:
            return GatewayVerdict(
                decision=PolicyDecision.DENY,
                reason=f"Blocked by adversarial screen: {threat}",
                risk_tier="CRITICAL",
                screened_threat=threat,
            )
        if self._has_shell_escape(arguments):
            return GatewayVerdict(
                decision=PolicyDecision.DENY,
                reason="Blocked by adversarial screen: COMMAND_ESCAPE_ATTEMPT",
                risk_tier="CRITICAL",
                screened_threat="COMMAND_ESCAPE_ATTEMPT",
            )

        # Gate 2: strict schema validation.
        is_valid, _, validation_msg = ActionValidator.validate_action(tool_name, arguments)
        if not is_valid:
            return GatewayVerdict(
                decision=PolicyDecision.DENY,
                reason=f"Blocked by schema validation: {validation_msg}",
                risk_tier="HIGH",
            )

        # Gate 3: deterministic policy evaluation.
        evaluation: ActionEvaluationResult = self.policy_engine.evaluate_action(
            tool_name=tool_name,
            arguments=arguments,
            session_id=session_id,
            agent_id=agent_id,
        )
        if evaluation.decision == PolicyDecision.DENY:
            return GatewayVerdict(
                decision=PolicyDecision.DENY,
                reason=f"Blocked by security policy: {evaluation.reason}",
                risk_tier=evaluation.risk_tier.value,
            )
        if evaluation.decision == PolicyDecision.REQUIRE_APPROVAL:
            if approval_nonce is None:
                return GatewayVerdict(
                    decision=PolicyDecision.REQUIRE_APPROVAL,
                    reason=f"Human approval required: {evaluation.reason}",
                    risk_tier=evaluation.risk_tier.value,
                )
            consumed = self.policy_engine.consume_approval(approval_nonce)
            if consumed is None:
                return GatewayVerdict(
                    decision=PolicyDecision.DENY,
                    reason="Blocked: invalid, expired, or replayed approval nonce",
                    risk_tier="CRITICAL",
                )
            return GatewayVerdict(
                decision=PolicyDecision.ALLOW,
                reason=f"Approved by human nonce for: {evaluation.reason}",
                risk_tier=evaluation.risk_tier.value,
                approval_consumed=True,
            )
        return GatewayVerdict(
            decision=PolicyDecision.ALLOW,
            reason=f"Permitted by security policy: {evaluation.reason}",
            risk_tier=evaluation.risk_tier.value,
        )

    def validate_agent_pick(self, agent_id: str, registered_ids: List[str]) -> bool:
        """Agent picks must name an already-registered agent. No grants."""
        return agent_id in registered_ids

    def validate_model_pick(self, provider_id: str, allowed_ids: List[str]) -> bool:
        """Model picks must stay inside the caller-approved allowlist."""
        return provider_id in allowed_ids
