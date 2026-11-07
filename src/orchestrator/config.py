"""Configuration management for WinAI-OE.

Enforces safe defaults, loopback-only bindings, strict environment validation,
and complete isolation of production credentials from source code.
"""

from enum import Enum
from pathlib import Path
import secrets
from typing import Literal, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentType(str, Enum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class SecurityLevel(str, Enum):
    STRICT = "strict"        # Approvals required for any write or process execution
    MODERATE = "moderate"    # Approvals for high-risk / external network calls only
    PERMISSIVE = "permissive"# For sandboxed automated testing only


class AppConfig(BaseSettings):
    """Immutable, validated system configuration."""
    model_config = SettingsConfigDict(
        env_prefix="WINAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Host Settings
    app_name: str = "WinAI Operating Environment"
    app_version: str = "0.1.0"
    environment: EnvironmentType = EnvironmentType.DEVELOPMENT
    security_level: SecurityLevel = SecurityLevel.STRICT

    # IPC & Networking (Strict loopback isolation)
    host: str = Field(default="127.0.0.1", description="Must bind to loopback address")
    port: int = Field(default=8765, ge=1024, le=65535, description="Local IPC port")
    ipc_token: str = Field(
        default_factory=lambda: secrets.token_hex(32),
        description="Session authentication token for loopback IPC",
    )

    # Workspace & File Paths
    workspace_root: Path = Field(
        default_factory=lambda: Path.cwd().resolve(),
        description="Root directory for authorized user file operations",
    )
    data_dir: Path = Field(
        default_factory=lambda: (Path.home() / ".winai" / "data").resolve(),
        description="Directory for local database and persistent state",
    )
    logs_dir: Path = Field(
        default_factory=lambda: (Path.home() / ".winai" / "logs").resolve(),
        description="Directory for structured audit and operational logs",
    )

    # Subprocess & Job Object Execution Limits
    subprocess_timeout_seconds: int = Field(default=60, ge=1, le=600)
    subprocess_max_memory_mb: int = Field(default=1024, ge=128, le=8192)
    subprocess_max_output_bytes: int = Field(default=51200, ge=1024, le=1048576)

    # Model Defaults
    default_provider: str = "mock"
    default_model: str = "mock-gpt-4o"
    context_window_tokens: int = Field(default=8192, ge=1024)

    @field_validator("host")
    @classmethod
    def validate_host_loopback(cls, v: str) -> str:
        """Enforce that host binds strictly to loopback addresses."""
        allowed = {"127.0.0.1", "localhost", "::1"}
        if v not in allowed:
            raise ValueError(
                f"Security violation: Binding to '{v}' is prohibited. "
                f"WinAI-OE must bind strictly to a loopback address ({allowed})."
            )
        return v

    @field_validator("workspace_root", "data_dir", "logs_dir")
    @classmethod
    def canonicalize_paths(cls, v: Path) -> Path:
        """Resolve symbolic links and relative tokens to absolute paths."""
        return v.resolve()

    def resource_limits_dict(self) -> dict:
        """Return the enforced subprocess and context budgets (no secrets)."""
        return {
            "subprocess_timeout_seconds": self.subprocess_timeout_seconds,
            "subprocess_max_memory_mb": self.subprocess_max_memory_mb,
            "subprocess_max_output_bytes": self.subprocess_max_output_bytes,
            "context_window_tokens": self.context_window_tokens,
        }

    def public_config_dict(self) -> dict:
        """Return the safe, non-secret subset for client-facing endpoints."""
        return {
            "environment": self.environment,
            "security_level": self.security_level,
            "workspace_root": str(self.workspace_root),
            "default_provider": self.default_provider,
            "default_model": self.default_model,
        }


def get_config(**overrides) -> AppConfig:
    """Load and return application settings with optional runtime overrides."""
    return AppConfig(**overrides)
