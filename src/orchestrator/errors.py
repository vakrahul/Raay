"""Application error hierarchy with structured fail-closed payloads."""


class WinAIError(Exception):
    """Base class for all WinAI-OE application errors."""

    def __init__(self, code: str, message: str, remediation: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation

    def to_dict(self) -> dict:
        return {
            "error_code": self.code,
            "message": self.message,
            "remediation": self.remediation,
        }


class SecurityViolationError(WinAIError):
    """Policy denials, traversal attempts, and approval forgery."""


class ToolValidationError(WinAIError):
    """Malformed tool payloads and schema violations."""


class ProviderError(WinAIError):
    """Upstream provider outages, rate limits, and timeouts."""


class ExecutionError(WinAIError):
    """Subprocess failures and unverifiable execution outcomes."""
