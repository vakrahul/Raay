"""Agent configuration interface: DB-backed settings the Windows agent actually reads.

The dispatcher consults this on every task (fresh DB read, no caching), so
admin changes take effect immediately without code changes or restarts.
"""

from typing import Any, Dict, List, Optional

from src.platform.control_db import ControlPlaneDB

KNOWN_AGENT_KEYS = {
    "agent_enabled",
    "default_provider",
    "default_model",
    "max_task_duration_seconds",
    "approval_policy",
    "log_level",
    "allowed_applications",
}

ASSIGNABLE_KEYS = {"agent_enabled", "default_provider", "approval_policy", "log_level"}


def get_effective_agent_config(db: ControlPlaneDB) -> Dict[str, Any]:
    """Resolve the live agent configuration from database settings + flags."""
    settings = db.get_settings(include_secrets=False)
    flags = db.get_flags()
    allowed = settings.get("allowed_applications")
    if not isinstance(allowed, list) or not allowed:
        allowed = ["notepad", "calc", "chrome", "vscode", "edge"]
    return {
        "agent_enabled": bool(settings.get("agent_enabled", True)),
        "default_provider": settings.get("default_provider", "mock"),
        "default_model": settings.get("default_model", "mock-gpt-4o"),
        "max_task_duration_seconds": int(settings.get("max_task_duration_seconds", 600)),
        "approval_policy": settings.get("approval_policy", "strict"),
        "log_level": settings.get("log_level", "INFO"),
        "allowed_applications": [str(a) for a in allowed],
        "feature_flags": {k: v["enabled"] for k, v in flags.items()},
    }


def extract_requested_apps(prompt: str, known_apps: Optional[List[str]] = None) -> List[str]:
    """Best-effort extraction of whitelisted app ids mentioned in a prompt."""
    candidates = known_apps or ["notepad", "calc", "chrome", "vscode", "edge", "paint", "calculator"]
    lowered = (prompt or "").lower()
    found = []
    for app in candidates:
        if app in lowered and app not in found:
            found.append("calc" if app == "calculator" else app)
    return found
