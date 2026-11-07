"""Execution sandbox, resource quotas, and network restrictions (Phases 71-74)."""
from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
from pydantic import BaseModel, Field


class ResourceQuota(BaseModel):
    max_memory_mb: int = Field(default=512, ge=64, le=4096)
    max_execution_seconds: int = Field(default=30, ge=1, le=300)
    max_disk_write_mb: int = Field(default=50, ge=1, le=500)


class NetworkPolicy(BaseModel):
    allow_outbound: bool = False
    allowed_domains: Set[str] = Field(default_factory=lambda: {"pypi.org", "python.org", "github.com"})

    def is_domain_allowed(self, url: str) -> bool:
        if not self.allow_outbound:
            return False
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or url
            for domain in self.allowed_domains:
                if hostname == domain or hostname.endswith("." + domain):
                    return True
            return False
        except Exception:
            return False


class IsolationSandbox:
    """Restricted execution environment for AI-generated code and scripts."""

    def __init__(
        self,
        sandbox_dir: Path,
        quota: Optional[ResourceQuota] = None,
        network_policy: Optional[NetworkPolicy] = None,
    ):
        self.sandbox_dir = sandbox_dir.resolve()
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.quota = quota or ResourceQuota()
        self.network_policy = network_policy or NetworkPolicy()

    def validate_network_egress(self, url: str) -> Tuple[bool, str]:
        """Check whether network call to URL is permitted by policy."""
        if not self.network_policy.allow_outbound:
            return False, "Security Violation: Outbound network access is disabled for this sandbox."
        if not self.network_policy.is_domain_allowed(url):
            return False, f"Security Violation: Target domain in '{url}' is not in the approved whitelist."
        return True, "Network egress approved by policy."

    def validate_file_write_quota(self, file_size_bytes: int) -> Tuple[bool, str]:
        """Ensure file write does not exceed disk quota."""
        max_bytes = self.quota.max_disk_write_mb * 1024 * 1024
        if file_size_bytes > max_bytes:
            return False, f"Resource limit exceeded: {file_size_bytes} bytes exceeds quota of {max_bytes} bytes."
        return True, "Within write quota."
