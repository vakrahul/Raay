"""Privilege separation and protected security configuration (Phases 75-78)."""
import os
from pathlib import Path
from typing import List, Set, Tuple


class PrivilegeGuard:
    """Guards security configuration and system credentials from agent tampering."""

    # Absolute protected files that agents can NEVER modify or read
    PROTECTED_FILENAMES = {
        "vault.enc",
        "security.json",
        "audit.jsonl",
        "id_rsa",
        "id_ed25519",
        "known_hosts",
        "SAM",
        "SYSTEM",
    }

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.system_security_dir = (self.workspace_root / ".winai").resolve()

    def is_access_permitted(self, file_path: Path, mode: str = "r") -> Tuple[bool, str]:
        """Verify that agent is not attempting to tamper with security configs or credentials."""
        try:
            canonical = file_path.resolve()
        except Exception as e:
            return False, f"Resolution error: {e}"

        # 1. Block protected filenames
        if canonical.name in self.PROTECTED_FILENAMES:
            return False, f"Security Violation: Target '{canonical.name}' is a protected security file."

        # 2. Block write/delete operations inside .winai security root
        if mode in ["w", "a", "d"]:
            if canonical == self.system_security_dir or self.system_security_dir in canonical.parents:
                # Only allow backups/ and trash/ subdirectories for controlled file service
                allowed_subdirs = [
                    self.system_security_dir / "backups",
                    self.system_security_dir / "trash",
                ]
                is_safe_subdir = any(sub == canonical or sub in canonical.parents for sub in allowed_subdirs)
                if not is_safe_subdir:
                    return False, "Security Violation: Agents cannot modify core security configuration."

        # 3. Block user home SSH / credential hives
        ssh_dir = (Path.home() / ".ssh").resolve()
        if canonical == ssh_dir or ssh_dir in canonical.parents:
            return False, "Security Violation: Access to user SSH credentials is strictly forbidden."

        return True, "Access permitted by Privilege Guard."
